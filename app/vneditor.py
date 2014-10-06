# vim: set fileencoding=utf-8 :

from google.appengine.api import users, urlfetch, taskqueue, app_identity
from google.appengine.api.mail import EmailMessage
from google.appengine.ext import blobstore

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

# Configuration
import access
import version

# Our libraries
import vnapi

# Set up URLfetch settings
urlfetch.set_default_fetch_deadline(60)

# Constants.
language_names_list = ['en', 'es', 'pt', 'de', 'fr', 'zh']
language_names = {
    'en': u'English',
    'es': u'Spanish (Español)',
    'pt': u'Portuguese (Português)',
    'de': u'German (Deutsch)',
    'fr': u'French (le Français)',
    'zh': u'Chinese (中文)'
}

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
            if lang in language_names:
                lookup_results_lang_names[lang] = language_names[lang]
            else:
                lookup_results_lang_names[lang] = lang

        # Calculate dataset coverage stats
        datasets = vnapi.getDatasets()
        datasets_coverage = {}
        for dataset in datasets:
            dname = dataset['dataset']

            datasets_coverage[dname] = dict()
            for lang in language_names:
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
            'language_names': language_names,
            'language_names_list': language_names_list,
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

        # Create file into gcs_bucket_name
        fgz = cStringIO.StringIO()
        csv_filename = "output.csv"
        gzfile = gzip.GzipFile(filename=csv_filename, mode='wb', fileobj=fgz)
        
        # Prepare csv writer.
        csvfile = csv.writer(gzfile)

        # Prepare to write out CSV.
        header = ['scientificname', 'tax_family', 'tax_order', 'tax_class']
        for lang in language_names_list:
            header.extend([lang, lang + '_source'])
        header.extend(['empty'])
        csvfile.writerow(header)

        def add_name(name, higher_taxonomy, sorted_names):
            row = [name, 
                "|".join(higher_taxonomy['family']),
                "|".join(higher_taxonomy['order']),
                "|".join(higher_taxonomy['class'])]

            for lang in language_names_list:
                if lang in sorted_names:
                    vname = sorted_names[lang]['vernacularname']
                    sources = sorted_names[lang]['sources']

                    row.extend([vname.encode('utf-8'), "|".join(sources).encode('utf-8')])
                else:
                    row.extend([None, None])

            csvfile.writerow(row)
        ListViewHandler.iterateOver(add_name)
        gzfile.close()

        # E-mail the response to someone.
        email = EmailMessage(sender = access.EMAIL_ADDRESS, to = access.EMAIL_ADDRESS, 
            subject = 'generate-taxonomy-translations response',
            body = 'Look! A file!',
            attachments = (csv_filename + ".gzip", fgz.getvalue()))
        email.send()

        self.response.set_status(200)
        self.response.out.write("OK")

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

        name_list = ListViewHandler.getNames(0, -1, lambda name: (name in iucn_reptile_names or name in iucn_amphibian_names) and random.randint(1, 8600) <= 250)

        self.render_template('list.html', {
            'vneditor_version': version.VNEDITOR_VERSION,
            'user_url': user_url,
            'search_criteria': search_criteria,
            'name_list': name_list,
            'language_names_list': language_names_list,
            'language_names': language_names
        }) 

    # Generate a list of accepted vernacular names for a list of scientific names.
    @staticmethod
    def getNames(name_from = 0, name_size = -1, fn_name_filter = lambda name: True):
        results = dict()

        def addToDict(name, higher_taxonomy, sorted_names):
            if name in results:
                raise RuntimeError("Duplicate name in getNames")

            results[name] = sorted_names

        ListViewHandler.iterateOver(addToDict, name_from, name_size, fn_name_filter)

        return results

    # Iterate over names for a particular list.
    @staticmethod
    def iterateOver(fn_name_iterate, name_from = 0, name_size = -1, fn_name_filter = lambda name: True):
        # Step 1. Obtain a list of every species name we need to generate.
        datasets = vnapi.getDatasets()
        all_names = set()
        for dataset in datasets:
            all_names.update(vnapi.getNamesInDataset(dataset['dataset']))

        # Filter names as instructed
        all_names = filter(fn_name_filter, sorted(all_names))

        # Limit names as instructed
        if name_size != -1:
            all_names = all_names[name_from:name_from + name_size + 1]
        else:
            all_names = all_names[name_from:]

        # Reassert set-ness.
        all_names = set(all_names)

        # From http://stackoverflow.com/a/312464/27310
        def chunks(items, size):
            for i in xrange(0, len(items), size):
                yield items[i:i+size]

        # end_at = 100000
        row = 0
        for names in chunks(sorted(all_names), 1000):
            row += len(names)

            logging.info("Downloaded %d rows of %d names." % (row, len(all_names)))

            #if row > end_at:
            #    break

            mol_source = "no longer in use" # TODO

            tax_family = ""
            tax_order = ""
            tax_class = ""
    
            # Get higher taxonomy, language, common name.
            scientificname_list = ", ".join(map(lambda x: vnapi.encode_b64_for_psql(x.lower()), names))
            sql = "SELECT binomial, array_agg(DISTINCT LOWER(tax_order)) AS agg_order, array_agg(DISTINCT LOWER(tax_class)) AS agg_class, array_agg(DISTINCT LOWER(tax_family)) AS agg_family, lang, cmname, array_agg(source) AS sources, array_agg(url) AS urls, MAX(updated_at) AS max_updated_at, MAX(source_priority) AS max_source_priority FROM %s WHERE LOWER(binomial) IN (%s) GROUP BY binomial, lang, cmname ORDER BY max_source_priority DESC, max_updated_at DESC"
            sql_query = sql % (
                access.ALL_NAMES_TABLE,
                scientificname_list
            )

            urlresponse = urlfetch.fetch(access.CDB_URL,
                payload=urllib.urlencode(dict(
                    q = sql_query
                )),
                method=urlfetch.POST,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
 
            if urlresponse.status_code != 200:
                self.response.set_status(500)
                self.response.out.write("<h2>Error during lookup of '" + ', '.join(names) + "': server returned error " + str(response.status_code) + ": " + str(response.content) + "</h2></html>")
                return
                
            results = json.loads(urlresponse.content)
            rows_by_name = vnapi.groupBy(results['rows'], 'binomial')

            def clean_agg(list):
                # This was already lowercased by the SQL
                no_blanks = filter(lambda x: x is not None and x != '', list)
                return set(no_blanks)

            for name in rows_by_name:
                results = rows_by_name[name]
                sorted_results = vnapi.sortNames(results)
                best_names = dict()
                taxonomy = {
                    'order': set(),
                    'class': set(),
                    'family': set()
                }

                for lang in sorted_results:
                    lang_results = sorted_results[lang]

                    if len(lang_results) == 0:
                        continue

                    best_names[lang] = {
                        'vernacularname': lang_results[0]['cmname'],
                        'sources': lang_results[0]['sources']
                    }

                    for result in lang_results:
                        taxonomy['order'].update(clean_agg(result['agg_order']))
                        taxonomy['class'].update(clean_agg(result['agg_class']))
                        taxonomy['family'].update(clean_agg(result['agg_family']))

                fn_name_iterate(name, taxonomy, best_names)

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/index.html', MainPage),
    ('/add/name', AddNameHandler),
    ('/page/private', StaticPages),
    ('/list', ListViewHandler),
    ('/generate/taxonomy_translations', GenerateTaxonomyTranslations)
], debug=not PROD)
