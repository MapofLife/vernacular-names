# vim: set fileencoding=utf-8 :

from google.appengine.api import users, urlfetch, taskqueue, app_identity
from google.appengine.api.mail import EmailMessage
from google.appengine.ext import blobstore

from titlecase import titlecase

import base64
import os
import webapp2
import jinja2
import json
import urllib
import re
import logging
import random
import cStringIO
import gzip
import csv
import time

# Configuration
import access
import version
import languages

# Our libraries
import vnapi
import vnnames

# Flags
FLAG_LOOKUP_GENERA = False

# Set up URLfetch settings
urlfetch.set_default_fetch_deadline(60)

# Check whether we're in production (PROD = True) or not.
if 'SERVER_SOFTWARE' in os.environ:
    PROD = not os.environ['SERVER_SOFTWARE'].startswith('Development')
else:
    PROD = True

# Set up the Jinja templating environment
JINJA_ENV = jinja2.Environment(
    loader = jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions = ['jinja2.ext.autoescape'],
    autoescape = True
)

# The BaseHandler sets up some basic routines that all pages can
# use.
class BaseHandler(webapp2.RequestHandler):
    # render_template renders a Jinja2 template from the 'templates' dir,
    # using the template arguments provided.
    def render_template(self, f, template_args):
        path = os.path.join("templates", f)
        template = JINJA_ENV.get_template(path)
        self.response.out.write(template.render(template_args))

    # check_user checks to see if the user should be allowed to access
    # this application (are they a signed in Google user who is an administrator
    # on this project?). If not, it redirects them to /page/private
    def check_user(self):
        user = users.get_current_user()
        if not user:
            self.redirect(users.create_login_url(self.request.uri))

        if not users.is_current_user_admin():
            self.redirect('/page/private')

        return user

# The StaticPages handler handles our static pages by providing them
# with a set of default variables.
class StaticPages(BaseHandler):
    def __init__(self, request, response):
        self.template_mappings = {
            '/': 'welcome.html',
            '/index.html': 'welcome.html',
            '/page/private': 'private.html'
        }
        self.initialize(request, response)

    def get(self):
        self.response.headers['Content-Type'] = 'text/html'

        path = self.request.path.lower()
        if path in self.template_mappings:
            self.render_template(self.template_mappings[path], {
                'login_url': users.create_login_url('/'),
                'logout_url': users.create_logout_url('/')
            })
        else:
            self.response.status = 404

# The MainPage executes searches and displays results.
class MainPage(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'
        
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        current_search = self.request.get('search')
        if self.request.get('clear') != '':
            current_search = ''
        current_search = current_search.strip()

        # Do the search.
        search_results = dict()
        search_results_scnames = []
        if current_search != '':
            search_results = vnapi.searchForName(current_search)
            search_results_scnames = sorted(search_results.keys())

        # Check for dataset_filter
        dataset_filter = self.request.get('dataset')
        if dataset_filter != '':
            if current_search != '':
                # If there is a search, filter it using dataset_filter.
                search_results_scnames = filter(
                    lambda scname: vnapi.datasetContainsName(dataset_filter, scname),
                    search_results_scnames
                )
            else:
                # If not, search by dataset.
                search_results_scnames = vnapi.getNamesInDataset(dataset_filter)

                search_results = dict()
                for scname in search_results_scnames:
                    search_results[scname] = []

        # Do the lookup
        lookup_search = self.request.get('lookup')
        lookup_results = {}

        # During the initial search, automatically pick identical matches.
        if lookup_search == '' and current_search != '':
            lookup_search = current_search

        if lookup_search != '':
            lookup_results = vnapi.getVernacularNames(lookup_search)

        lookup_results_lang_names = dict()
        for lang in lookup_results:
            if lang in languages.language_names:
                lookup_results_lang_names[lang] = languages.language_names[lang]
            else:
                lookup_results_lang_names[lang] = lang

        # Calculate dataset coverage stats
        datasets = vnapi.getDatasets()
        datasets_coverage = {}
        for dataset in datasets:
            dname = dataset['dataset']

            datasets_coverage[dname] = dict()
            for lang in languages.language_names:
                datasets_coverage[dname][lang] = vnapi.getDatasetCoverage(dname, lang)

        self.render_template('main.html', {
            'message': self.request.get('msg'),
            'datasets': datasets,
            'datasets_coverage': datasets_coverage,
            'dataset_filter': dataset_filter,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'current_search': current_search,
            'search_results': search_results,
            'search_results_scnames': search_results_scnames,
            'lookup_search': lookup_search,
            'lookup_results': lookup_results,
            'lookup_results_languages': sorted(lookup_results.keys()),
            'lookup_results_language_names': lookup_results_lang_names,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION
        })


class AddNameHandler(BaseHandler):
    def post(self):
        # Fail without login.
        current_user = self.check_user()

        # Retrieve state
        search = self.request.get('search')
        lookup = self.request.get('lookup')

        # Retrieve name to add
        scname = lookup
        cmname = self.request.get('name_to_add')
        lang = self.request.get('lang')
        source = self.request.get('source')
        try:
            source_priority = int(self.request.get('priority'))
        except ValueError:
            # All manual sources come in with a default priority of 80
            source_priority = 80

        # Metadata
        added_by = current_user.nickname()

        # Base64 anything we don't absolutely trust
        added_by_b64 = vnapi.encode_b64_for_psql(added_by)
        scname_b64 = vnapi.encode_b64_for_psql(scname)
        cmname_b64 = vnapi.encode_b64_for_psql(cmname)
        lang_b64 = vnapi.encode_b64_for_psql(lang)
        source_b64 = vnapi.encode_b64_for_psql(source)

        # Synthesize SQL
        sql = "INSERT INTO %s (added_by, scname, lang, cmname, url, source, source_url, source_priority) VALUES (%s, %s, %s, %s, NULL, %s, 'https://github.com/gaurav/vernacular-names', %d);"
        sql_query = sql % (
            access.ALL_NAMES_TABLE,
            added_by_b64,
            scname_b64,
            lang_b64,
            cmname_b64,
            source_b64,
            source_priority
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL % urllib.urlencode(
            dict(
                q = sql_query,
                api_key = access.CARTODB_API_KEY
            )
        ))

        if response.status_code != 200:
            message = "Error: server returned error " + response.status_code + ": " + response.content
        else:
            message = "Name added to language '" + lang + "'."

        # Redirect to the main page.
        self.redirect("/?" + urllib.urlencode(dict(
            msg = message,
            search = search,
            lookup = lookup
        )) + "#lang-" + lang)

class GenerateTaxonomyTranslations(BaseHandler):
    # Activates the taxonomy_translations taskqueue task.
    def get(self):
        task = taskqueue.add(url='/generate/taxonomy_translations', queue_name='generate-taxonomy-translations', method='POST')

        self.response.set_status(200)
        self.response.out.write("OK queued (" + task.name + ")")

    # Task! Should be run on the 'generate-taxonomy-translations'
    def post(self): 
        # We have no parameters. We just generate.
        # Fail without login.
        current_user = self.check_user()

        # Create file in memory and make it gzipped.
        fgz = cStringIO.StringIO()
        csv_filename = "taxonomy_translations_" + time.strftime("%Y_%B_%d_%H%MZ", time.gmtime())  + ".csv"
        gzfile = gzip.GzipFile(filename=csv_filename, mode='wb', fileobj=fgz)
        
        # Prepare csv writer.
        csvfile = csv.writer(gzfile)

        # Get a list of every name in the master list.
        datasets = vnapi.getDatasets()
        all_names = set()
        for dataset in datasets:
            all_names.update(vnapi.getNamesInDataset(dataset['dataset']))
        
        # Prepare to write out CSV.
        header = ['scientificname', 'tax_family', 'tax_order', 'tax_class']
        for lang in languages.language_names_list:
            header.extend([lang + '_name', lang + '_source', lang + '_family', lang + '_order', lang + '_class'])
        header.extend(['empty'])
        csvfile.writerow(header)

        def format_name(name):
            # This slows us by about 50%.
            return name #titlecase(name)

        def concat_names(names):
            return "|".join(map(sorted(names))).encode('utf-8')

        def add_name(name, higher_taxonomy, vnames_by_lang):
            row = [name, 
                "|".join(sorted(higher_taxonomy['family'])),
                "|".join(sorted(higher_taxonomy['order'])),
                "|".join(sorted(higher_taxonomy['class']))]

            for lang in languages.language_names_list:
                if lang in vnames_by_lang:
                    vname = vnames_by_lang[lang].vernacularname
                    sources = vnames_by_lang[lang].sources

                    row.extend([
                        vname.encode('utf-8'), 
                        "|".join(sorted(sources)).encode('utf-8'),
                        "|".join(sorted(vnames_by_lang[lang].tax_family)).encode('utf-8'),
                        "|".join(sorted(vnames_by_lang[lang].tax_order)).encode('utf-8'),
                        "|".join(sorted(vnames_by_lang[lang].tax_class)).encode('utf-8')
                    ])
                else:
                    row.extend([None, None, None, None, None])

            csvfile.writerow(row)
        vnnames.searchVernacularNames(add_name, all_names)
        gzfile.close()

        # E-mail the response to someone.
        email = EmailMessage(sender = access.EMAIL_ADDRESS, to = access.EMAIL_ADDRESS,
            subject = 'Taxonomy translations download',
            body = 'This taxonomy_translations file was prepared at ' + time.strftime("%x %X %Z", time.gmtime()) + '.',
            attachments = (csv_filename + ".gzip", fgz.getvalue()))
        email.send()

        self.response.set_status(200)
        self.response.out.write("OK")

# TODO fix hack
vnameCache = dict()

# Display a section of the Big List as a table.
class ListViewHandler(BaseHandler):
    # Display
    def get(self):
        self.response.headers['Content-type'] = 'text/html'

        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        # List iucn_amphibian and iucn_reptiles
        search_criteria = "Listing iucn_amphibian and iucn_reptiles"

        iucn_reptile_names = set(vnapi.getNamesInDataset('iucn_reptiles'))
        iucn_amphibian_names = set(vnapi.getNamesInDataset('iucn_amphibian'))

        # name_list = ListViewHandler.getNames(0, -1, lambda name: (name in iucn_reptile_names or name in iucn_amphibian_names) and random.randint(1, 8600) <= 250)

        self.render_template('list.html', {
            'vneditor_version': version.VNEDITOR_VERSION,
            'user_url': user_url,
            'search_criteria': search_criteria,
            'name_list': name_list,
            'language_names_list': languages.language_names_list,
            'language_names': languages.language_names
        }) 

    # Iterate over names for a particular list.
    @staticmethod
    def iterateOver(fn_name_iterate, name_from = 0, name_size = -1, fn_name_filter = lambda name: True):
        # Filter names as instructed
        all_names = filter(fn_name_filter, sorted(all_names))

        # Limit names as instructed
        if name_size != -1:
            all_names = all_names[name_from:name_from + name_size + 1]
        else:
            all_names = all_names[name_from:]

        return vnnames.searchVernacularNames(fn_name_iterate, all_names)

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/index.html', MainPage),
    ('/add/name', AddNameHandler),
    ('/page/private', StaticPages),
    ('/list', ListViewHandler),
    ('/generate/taxonomy_translations', GenerateTaxonomyTranslations)
], debug=not PROD)
