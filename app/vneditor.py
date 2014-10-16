# vim: set fileencoding=utf-8 :

from google.appengine.api import users, urlfetch, taskqueue
from google.appengine.api.mail import EmailMessage

import webapp2
import jinja2
import urllib
import re
import logging
import cStringIO
import gzip
import csv
import time
import os
import json

import access
import version
import languages
import vnapi
import vnnames

# Configuration

# Display the total count in /list: expensive, but useful.
FLAG_LIST_DISPLAY_COUNT = True

# What the 'source' should be when adding new rows.
SOURCE_URL = "https://github.com/gaurav/vernacular-names"

# How long to wait for urlfetch to return (60 seconds is the maximum).
urlfetch.set_default_fetch_deadline(60)

# Check whether we're in production mode (PROD = True) or not.
PROD = True
if 'SERVER_SOFTWARE' in os.environ:
    PROD = not os.environ['SERVER_SOFTWARE'].startswith('Development')

# Set up the Jinja templating environment
JINJA_ENV = jinja2.Environment(
    loader = jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions = ['jinja2.ext.autoescape'],
    autoescape = True
)

# The BaseHandler sets up some basic routines that all pages use.
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
        
        # Set up user details.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        # Load the current search term.
        current_search = self.request.get('search')
        if self.request.get('clear') != '':
            current_search = ''
        current_search = current_search.strip()

        # Load the scientific name being looked up. If no search is
        # currently in progress, look up the common name instead.
        lookup_search = self.request.get('lookup')
        lookup_results = {}

        # If the current search is blank, but lookup is not, search
        # for that instead.
        if current_search == '' and lookup_search != '':
            current_search = lookup_search

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

        # During the initial search, automatically pick identical matches.
        if lookup_search == '' and current_search != '':
            lookup_search = current_search

        # Find all names for all species in the languages of interest.
        lookup_results_languages = []
        lookup_results_lang_names = dict()
        if lookup_search != '':
            lookup_results = vnnames.getVernacularNames([lookup_search], flag_all_results=True, flag_no_memoize=True, flag_lookup_genera=False)

            lookup_results_languages = lookup_results[lookup_search]

            for lang in lookup_results_languages:
                if lang in languages.language_names:
                    lookup_results_lang_names[lang] = languages.language_names[lang]
                else:
                    lookup_results_lang_names[lang] = lang

        # Get list of datasets
        datasets = vnapi.getDatasets()

        # Render the main template.
        self.render_template('main.html', {
            'message': self.request.get('msg'),
            'datasets_data': datasets,
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
            'lookup_results_languages': lookup_results_languages,
            'lookup_results_language_names': lookup_results_lang_names,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION
        })

# This handler will delete a row using its CartoDB identifier.
class DeleteByCDBIDHandler(BaseHandler):
    def post(self):
        # Fail without login.
        current_user = self.check_user()

        # Retrieve cartodb_id to delete.
        cartodb_id = int(self.request.get('cartodb_id'))

        # Synthesize SQL
        sql = "DELETE FROM %s WHERE cartodb_id=%d"
        sql_query = sql % (
            access.ALL_NAMES_TABLE,
            cartodb_id
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL % urllib.urlencode(
            dict(
                q = sql_query,
                api_key = access.CARTODB_API_KEY
            )
        ))

        if response.status_code != 200:
            message = "Error: server returned error " + str(response.status_code) + ": " + response.content
        else:
            message = "Change %d deleted successfully." % cartodb_id

        # Redirect to the recent changes page.
        self.redirect("/recent?" + urllib.urlencode(dict(
            msg = message,
        )))

# This handler will add a new name to the main table in CartoDB.
class AddNameHandler(BaseHandler):
    def post(self):
        # Fail without login.
        current_user = self.check_user()

        # Retrieve state. We only use this for the final redirect.
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
        sql = "INSERT INTO %s (added_by, scname, lang, cmname, url, source, source_url, source_priority) VALUES (%s, %s, %s, %s, NULL, %s, '" + SOURCE_URL + "', %d);"
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
            message = "Error: server returned error " + str(response.status_code) + ": " + response.content
        else:
            message = "Name added to language '" + lang + "'."

        # Redirect to the main page.
        self.redirect("/?" + urllib.urlencode(dict(
            msg = message,
            search = search,
            lookup = lookup
        )) + "#lang-" + lang)

# This handler generates the taxonomy_translations table. Eventually, we'd
# like to use something similar to export names from /list (https://github.com/gaurav/vernacular-names/issues/45),
# but I don't know how closely that's going to align with this.
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
            all_names.update(vnapi.getDatasetNames(dataset['dataset']))
        
        # Prepare to write out CSV.
        header = ['scientificname', 'tax_family', 'tax_order', 'tax_class']
        for lang in languages.language_names_list:
            header.extend([lang + '_name', lang + '_source', lang + '_family', lang + '_order', lang + '_class'])
        header.extend(['empty'])
        csvfile.writerow(header)

        def concat_names(names):
            return "|".join(sorted(names)).encode('utf-8')

        def add_name(name, higher_taxonomy, vnames_by_lang):
            row = [name.capitalize(), 
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
                        concat_names(vnames_by_lang[lang].tax_family),
                        concat_names(vnames_by_lang[lang].tax_order),
                        concat_names(vnames_by_lang[lang].tax_class)
                    ])
                else:
                    row.extend([None, None, None, None, None])

            csvfile.writerow(row)
        
        # searchVernacularNames doesn't use the cache, but it calls 
        # getVernacularNames for higher taxonomy, which does.
        vnnames.clearVernacularNamesCache()
        vnnames.searchVernacularNames(add_name, all_names, flag_format_cmnames=True)

        # File completed!
        gzfile.close()

        # E-mail the response to me.
        settings = ""
        if vnnames.FLAG_LOOKUP_GENERA:
            settings = " with genera lookups turned on"

        email = EmailMessage(sender = access.EMAIL_ADDRESS, to = access.EMAIL_ADDRESS,
            subject = 'Taxonomy translations download',
            body = 'This taxonomy_translations file was prepared at ' + time.strftime("%x %X %Z", time.gmtime()) + settings + '.',
            attachments = (csv_filename + ".gzip", fgz.getvalue()))
        email.send()

        self.response.set_status(200)
        self.response.out.write("OK")

# Display a summary of the coverage by dataset and language.
class CoverageViewHandler(BaseHandler):
    # Display
    def get(self):
        self.response.headers['Content-type'] = 'text/html'

        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        # Stats are per-dataset, per-language.
        datasets = vnapi.getDatasets()
        datasets_coverage = {}
        for dataset in datasets:
            dname = dataset['dataset']

            # Get coverage information on all languages at once.
            datasets_coverage[dname] = dict()
            coverage = vnapi.getDatasetCoverage(dname, languages.language_names_list)

            for lang in languages.language_names_list:
                # TODO: move this into the template.
                datasets_coverage[dname][lang] = """
                    %d have species common names (%.2f%%)<br>
                    %d have genus common names (%.2f%%)<br>
                    %d have <a href="/list?dataset=%s&blank_lang=%s">no common names</a> (%.2f%%)
                    <!-- Total: %d -->
                """ % (
                    coverage[lang]['matched_with_species_name'],
                    int(coverage[lang]['matched_with_species_name']) / float(coverage[lang]['total']) * 100,
                    coverage[lang]['matched_with_genus_name'],
                    int(coverage[lang]['matched_with_genus_name']) / float(coverage[lang]['total']) * 100,
                    coverage[lang]['unmatched'],
                    dname, lang,
                    int(coverage[lang]['unmatched']) / float(coverage[lang]['total']) * 100,
                    coverage[lang]['total'] 
                )

        # Render coverage template.
        self.render_template('coverage.html', {
            'vneditor_version': version.VNEDITOR_VERSION,
            'user_url': user_url,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'language_names_list': languages.language_names_list,
            'language_names': languages.language_names,
            'datasets_data': datasets,
            'datasets_coverage': datasets_coverage
        }) 

# Return a list of recent changes, and allow some to be deleted.
class RecentChangesHandler(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'
        
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Is there an offset?
        offset = self.request.get_range('offset', 0, default=0)
        display_count = 100

        # Synthesize SQL
        recent_sql = ("""SELECT cartodb_id, scname, lang, cmname, source, url, source_priority, added_by, created_at, updated_at,
            COUNT(*) OVER() AS total_count
            FROM %s
            WHERE source_url='""" + SOURCE_URL + """'
            ORDER BY updated_at DESC
            LIMIT %d OFFSET %d
        """) % (
            access.ALL_NAMES_TABLE,
            display_count,
            offset
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(
                dict(
                    q = recent_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        recent_changes = []
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + list_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            recent_changes = results['rows']
            if len(recent_changes) > 0:
                total_count = recent_changes[0]['total_count']

        # Render recent changes.
        self.render_template('recent.html', {
            'message': message,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'datasets_data': vnapi.getDatasets(),
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'offset': offset,
            'display_count': display_count,
            'total_count': total_count,
            'recent_changes': recent_changes,
            'vneditor_version': version.VNEDITOR_VERSION
        })

# Display a section of the Big List as a table.
class ListViewHandler(BaseHandler):
    # Filter by datasets.
    def filterByDatasets(self, request, results):
        datasets = request.get_all('dataset')
        if 'all' in datasets:
            datasets = []

        if len(datasets) > 0:
            results['select'].append("array_agg(DISTINCT LOWER(dataset))")

        sql_having = []
        for dataset in datasets: 
            sql_having.append(vnapi.encode_b64_for_psql(dataset.lower()) + " = ANY(array_agg(LOWER(dataset)))")
            results['search_criteria'].append("filter by dataset '" + dataset + "'")

        if len(sql_having) > 0:
            results['having'].append("(" + " OR ".join(sql_having) + ")")

    # Filter by blank languages.
    def filterByBlankLangs(self, request, results):
        blank_langs = request.get_all('blank_lang')
        if 'none' in blank_langs:
            blank_langs = []

        if len(blank_langs) > 0:
            results['select'].append("array_agg(DISTINCT LOWER(lang))")

        sql_having = []
        for lang in blank_langs:
            sql_having.append("NOT " + vnapi.encode_b64_for_psql(lang.lower()) + " = ANY(array_agg(LOWER(lang)))")
            results['search_criteria'].append("filter by language '" + lang + "' being blank")

        if len(sql_having) > 0:
            results['having'].append("(" + " AND ".join(sql_having) + ")")

    # Display
    def get(self):
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        # Okay, so here's how this is going to work:
        # 1.    We pass the request object to a series of filters, which will
        #       return: (1) a string representation of what they have filtered,
        #       (2) an SQL string for use in a WHERE query, and (3) an SQL string
        #       for use in a HAVING query.
        # 2.    We synthesize this into an SQL query and send it to the server.
        #       Most importantly we tack on ORDER, LIMIT and OFFSET so that users
        #       can navigate within the table.
        # 3.    This query returns a list of species names. We send those to
        #       getVernacularNames()
        # 4.    We provide links for further navigation, filtering, or whatevs.

        # Get arguments.
        # Because of the way we handle these, they may show up multiple times.
        # In this case, we want the last one.
        def use_last_or_default(argname, default):
            all_vals = self.request.get_all(argname)
            if len(all_vals) == 0:
                return default
            else:
                return all_vals[-1]

        # Get offset and display_count.
        offset = int(use_last_or_default("offset", 0))
        display_count = int(use_last_or_default("display", 20))

        # We hand this results object to each filter function, and allow it
        # to modify it as it sees fit based on the request.
        results = {
            "search_criteria": [],
            "select": [],
            "where": [],
            "having": []
        }

        self.filterByDatasets(self.request, results)
        self.filterByBlankLangs(self.request, results)

        # There's an implicit first filter if there is no filter.
        if len(results['search_criteria']) == 0:
            results['search_criteria'] = ["List all"]
        else:
            results['search_criteria'][0].capitalize()

        # Every query should include scientificname and the total count.
        results['select'].insert(0, "scientificname")
        if FLAG_LIST_DISPLAY_COUNT:
            results['select'].insert(1,  "COUNT(*) OVER() AS total_count")

        # Build SELECT statement.
        select = ", ".join(results['select'])
        where = " AND ".join(results['where'])
        having = " AND ".join(results['having'])
        order_by = "ORDER BY scientificname ASC"
        results['search_criteria'].append("sorted by ascending  scientific name")

        limit_offset = "LIMIT %d OFFSET %d" % (
            display_count,
            offset
        )

        search_criteria = ", ".join(results['search_criteria'])

        # Put all the pieces of the SELECT statement together.
        list_sql = """SELECT
            %s
            FROM %s INNER JOIN %s ON (LOWER(scname) = LOWER(scientificname))
            %s
            GROUP BY scientificname
            %s
            %s
            %s
        """ % (
            select,
            access.MASTER_LIST, access.ALL_NAMES_TABLE,
            "WHERE " + where if where != "" else "",
            "HAVING " + having if having != "" else "",
            order_by,
            limit_offset
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(
                dict(
                    q = list_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Process error message or results.
        message = ""
        if response.status_code != 200:
            message = "<strong>Error</strong>: query ('" + list_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            message = "DEBUG: '" + list_sql + "'"
            results = json.loads(response.content)

        name_list = map(lambda x: x['scientificname'], results['rows'])
        total_count = 0
        if FLAG_LIST_DISPLAY_COUNT and len(results['rows']) > 0:
            total_count = results['rows'][0]['total_count']

        vnames = vnnames.getVernacularNames(name_list, flag_no_higher=True, flag_no_memoize=True, flag_all_results=False, flag_lookup_genera=True, flag_format_cmnames=True)

        self.render_template('list.html', {
            'vneditor_version': version.VNEDITOR_VERSION,
            'user_url': user_url,
            'language_names_list': languages.language_names_list,
            'language_names': languages.language_names,
            'datasets_data': vnapi.getDatasets(),
            'selected_datasets': set(self.request.get_all('dataset')),
            'selected_blank_langs': set(self.request.get_all('blank_lang')),
            'message': message,
            'search_criteria': search_criteria,
            'name_list': name_list,
            'vnames': vnames,
            'offset': offset,
            'display_count': display_count,
            'total_count': total_count
        }) 

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/index.html', MainPage),
    ('/add/name', AddNameHandler),
    ('/page/private', StaticPages),
    ('/list', ListViewHandler),
    ('/delete/cartodb_id', DeleteByCDBIDHandler),
    ('/recent', RecentChangesHandler),
    ('/coverage', CoverageViewHandler),
    ('/generate/taxonomy_translations', GenerateTaxonomyTranslations)
], debug=not PROD)
