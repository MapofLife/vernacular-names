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

# Display DEBUG information?
FLAG_DEBUG = False

# Display the total count in /list: expensive, but useful.
FLAG_LIST_DISPLAY_COUNT = True

# How many rows to display in /list by default.
LISTVIEWHANDLER_DEFAULT_ROWS = 500

# Sources with fewer vname entries than this are considered to be individual imports;
# greater than this are bulk imports.
INDIVIDUAL_IMPORT_LIMIT = 100

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
                'logout_url': users.create_logout_url('/'),
                'vneditor_version': version.VNEDITOR_VERSION
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

        if user is None:
            return

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
        tax_family = set()
        tax_order = set()
        tax_class = set()
        lookup_results_languages = []
        lookup_results_lang_names = dict()
        if lookup_search != '':
            lookup_results = vnnames.getVernacularNames([lookup_search], languages.language_names_list, flag_all_results=True, flag_no_memoize=True, flag_lookup_genera=False)

            # Summarize higher taxonomy.
            tax_family = lookup_results[lookup_search]['tax_family']
            tax_order = lookup_results[lookup_search]['tax_order']
            tax_class = lookup_results[lookup_search]['tax_class']

        # Render the main template.
        self.render_template('main.html', {
            'message': self.request.get('msg'),
            'dataset_filter': dataset_filter,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'current_search': current_search,
            'search_results': search_results,
            'search_results_scnames': search_results_scnames,
            'tax_family': tax_family,
            'tax_order': tax_order,
            'tax_class': tax_class,
            'lookup_search': lookup_search,
            'lookup_results': lookup_results,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION
        })

# This handler will delete a row using its CartoDB identifier.
class DeleteByCDBIDHandler(BaseHandler):
    def post(self):
        # Fail without login.
        current_user = self.check_user()

        if current_user is None:
            return

        # Retrieve cartodb_id to delete.
        cartodb_id = self.request.get_range('cartodb_id')

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

        if current_user is None:
            return

        # Retrieve state. We only use this for the final redirect.
        search = self.request.get('search')
        lookup = self.request.get('lookup')

        # Retrieve name to add
        scname = lookup
        cmname = self.request.get('name_to_add')
        lang = self.request.get('lang').lower()
        source = self.request.get('source')
        source_priority = self.request.get_range('priority', vnapi.PRIORITY_MIN, vnapi.PRIORITY_MAX, vnapi.PRIORITY_DEFAULT_APP)

        tax_class = self.request.get('tax_class')
        tax_order = self.request.get('tax_order')
        tax_family = self.request.get('tax_family')

        if scname == '':
            message = "Error: scientific name is blank."
        elif source == '':
            message = "Error: source is blank."
        # No common name or lang is fine, as long as we have higher taxonomy instead.
        elif cmname == '' and not (tax_class != '' or tax_order != '' or tax_family != ''):
            message = "Error: vernacular name is blank."
        elif lang == '' and not (tax_class != '' or tax_order != '' or tax_family != ''):
            message = "Error: language is blank."
        else:
            # Metadata
            added_by = current_user.nickname()

            # Base64 anything we don't absolutely trust
            added_by_b64 = vnapi.encode_b64_for_psql(added_by)
            scname_b64 = vnapi.encode_b64_for_psql(scname)
            cmname_b64 = vnapi.encode_b64_for_psql(cmname)
            lang_b64 = vnapi.encode_b64_for_psql(lang)
            source_b64 = vnapi.encode_b64_for_psql(source)

            tax_class_b64 = vnapi.encode_b64_for_psql(tax_class.strip().lower())
            tax_order_b64 = vnapi.encode_b64_for_psql(tax_order.strip().lower())
            tax_family_b64 = vnapi.encode_b64_for_psql(tax_family.strip().lower())

            # Synthesize SQL
            sql = "INSERT INTO %s (added_by, scname, lang, cmname, url, source, source_url, source_priority, tax_class, tax_order, tax_family) VALUES (%s, %s, %s, %s, NULL, %s, '" + SOURCE_URL + "', %d, %s, %s, %s);"
            sql_query = sql % (
                access.ALL_NAMES_TABLE,
                added_by_b64,
                scname_b64,
                lang_b64,
                cmname_b64,
                source_b64,
                source_priority,
                tax_class_b64,
                tax_order_b64,
                tax_family_b64
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
        #current_user = self.check_user()

        #if current_user is None:
        #    return

        # Create file in memory and make it gzipped.
        fgz = cStringIO.StringIO()
        csv_filename = "taxonomy_translations_" + time.strftime("%Y_%B_%d_%H%MZ", time.gmtime())  + ".csv"
        gzfile = gzip.GzipFile(filename=csv_filename, mode='wb', fileobj=fgz)
        
        # Prepare csv writer.
        csvfile = csv.writer(gzfile)

        # Get a list of every name in the master list.
        all_names = vnapi.getMasterList()
        
        # Prepare to write out CSV.
        header = ['scientificname', 'tax_family', 'tax_order', 'tax_class']
        for lang in languages.language_names_list:
            header.extend([lang + '_name', lang + '_source', lang + '_family'])
        header.extend(['empty'])
        csvfile.writerow(header)

        def concat_names(names):
            return "|".join(sorted(names)).encode('utf-8')

        def add_name(name, higher_taxonomy, vnames_by_lang):
            row = [name.encode('utf-8').capitalize(), 
                concat_names(higher_taxonomy['family']),
                concat_names(higher_taxonomy['order']),
                concat_names(higher_taxonomy['class'])]

            for lang in languages.language_names_list:
                if lang in vnames_by_lang:
                    vname = vnames_by_lang[lang].vernacularname
                    sources = vnames_by_lang[lang].sources
                    tax_family = vnames_by_lang[lang].tax_family

                    # Use family latin name instead of common name 
                    # if we don't have one.
                    if len(tax_family) == 0:
                        tax_family = map(lambda x: x.capitalize(), higher_taxonomy['family'])

                    row.extend([
                        vname.encode('utf-8'), 
                        concat_names(sources),
                        concat_names(tax_family)
                    ])
                else:
                    row.extend([None, None, None])

            csvfile.writerow(row)
        
        # searchVernacularNames doesn't use the cache, but it calls 
        # getVernacularNames for higher taxonomy, which does.
        vnnames.clearVernacularNamesCache()
        vnnames.searchVernacularNames(add_name, all_names, languages.language_names_list, flag_format_cmnames=True)

        # File completed!
        gzfile.close()

        # E-mail the response to me.
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

        if user is None:
            return

        # Pagination
        offset = int(self.request.get('offset', 0))
        default_display = 50
        display = int(self.request.get('display', default_display))

        langs = languages.language_names_list

        # Display 'display', offset by offset.
        all_datasets = vnapi.getDatasets()
        datasets = all_datasets[offset:offset+display]

        dataset_names = map(lambda x: x['dataset'], all_datasets)
        coverage = vnapi.getDatasetCoverage(dataset_names, langs)
        datasets_count = coverage['num_species']
        datasets_coverage = coverage['coverage']

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
            'datasets': datasets,
            'datasets_count': datasets_count,
            'datasets_coverage': datasets_coverage,
            'default_display': default_display,
            'display': display,
            'offset': offset
        }) 

# Lists the sources and their priorities, and (eventually) allows you to change them.
class SourcesHandler(BaseHandler):
    def post(self):
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Set up error msg.
        msg = ""

        # Retrieve source to modify
        source = self.request.get('source')
        try:
            source_priority = int(self.request.get('source_priority'))
        except ValueError:
            source_priority = -1

        if source_priority > 0 and source_priority < 1000:
            # Synthesize SQL
            sql = "UPDATE %s SET source_priority = %d WHERE source=%s"
            sql_query = sql % (
                access.ALL_NAMES_TABLE,
                source_priority,
                vnapi.encode_b64_for_psql(source)
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
                message = "Source priority modified to %d." % (source_priority)
        else:
            message = "Could not parse source priority, please try again."

        # Redirect to the main page.
        self.redirect("/sources?" + urllib.urlencode(dict(
            msg = message,
        )))

    def get(self):
        self.response.headers['Content-type'] = 'text/html'
        
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Is there an offset?
        offset = self.request.get_range('offset', 0, default=0)
        display_count = 100

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Synthesize SQL
        source_sql = ("""SELECT 
            source, 
            COUNT(*) OVER() AS total_count,
            COUNT(*) AS vname_count,
            COUNT(DISTINCT LOWER(scname)) AS scname_count, 
            array_agg(DISTINCT source_priority) AS agg_source_priority,
            MAX(source_priority) AS max_source_priority,
            array_agg(DISTINCT LOWER(lang)) AS agg_lang,
            array_agg(DISTINCT LOWER(tax_family)) AS agg_family,
            array_agg(DISTINCT LOWER(tax_order)) AS agg_order,
            array_agg(DISTINCT LOWER(tax_class)) AS agg_class
            FROM %s 
            GROUP BY source 
            ORDER BY 
                max_source_priority DESC,
                vname_count DESC,
                source ASC
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
                    q = source_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + source_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
            sources = []
        else:
            results = json.loads(response.content)
            sources = results['rows']
            if len(sources) > 0:
                total_count = sources[0]['total_count']

        # There are two kinds of sources:
        #   1. Anything <=1 is an individual import from the source.
        #       These should be grouped by prefix.
        #   2. Anything >1 is a bulk import, and should be displayed separately.
        individual_imports = filter(lambda x: int(x['vname_count']) <= INDIVIDUAL_IMPORT_LIMIT, sources)
        bulk_imports = filter(lambda x: int(x['vname_count']) > INDIVIDUAL_IMPORT_LIMIT, sources)

        # Render sources.
        self.render_template('sources.html', {
            'message': message,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,

            'offset': offset,
            'display_count': display_count,

            'total_count': total_count,
            'individual_imports': individual_imports,
            'bulk_imports': bulk_imports,

            'vneditor_version': version.VNEDITOR_VERSION
        })

# Display and edit the master list.
class MasterListHandler(BaseHandler):
    def get(self):
        self.post()

    def post(self):
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Any message?
        message = self.request.get('message')

        # Retrieve master list.
        dataset_filter = self.request.get('dataset')
        datasets_data = vnapi.getDatasets()
        if dataset_filter == '':
            datasets = map(lambda x: x['dataset'], datasets_data)
        else:
            datasets = set([dataset_filter])

        species = dict()
        for dataset in datasets:
            scnames = vnapi.getDatasetNames(dataset)

            for scname in scnames:
                scname = scname.lower()
                if scname in species:
                    species[scname]['datasets'].add(dataset)
                else:
                    species[scname] = dict(
                        datasets=set([dataset])
                    )

        scnames = sorted(species.keys())
    
        # Do a diff.
        diff_names = self.request.get('diff_names')
        diff_names_count = 0
        names_added = []
        names_deleted = []
        if diff_names != '':
            names = set(map(lambda x: x.lower(), re.split("\s*\n+\s*", diff_names)))

            diff_names_count = len(names)

            # How many names does the definitive list have that we don't?
            names_added = names.difference(scnames)
            names_deleted = set(scnames).difference(names)

        # print("names_added = " + ", ".join(names_added))
        # print("names_deleted = " + ", ".join(names_deleted))

        # Generate SQL statements
        sql_statements = ""
        if len(names_added) > 0:
            sql_statements += "INSERT INTO %s (dataset, scientificname) VALUES\n" % (
                access.MASTER_LIST
            )
            for name in names_added:
                sql_statements += "\t(%s, %s),\n" % (
                    vnapi.encode_b64_for_psql(dataset_filter),
                    vnapi.encode_b64_for_psql(name.capitalize()),
                )

            sql_statements = sql_statements.rstrip(",\n")
            sql_statements += "\n\n"

        if len(names_deleted) > 0:
            sql_statements += "DELETE FROM %s WHERE\n" % (
                access.MASTER_LIST
            )
            for name in names_deleted:
                sql_statements += "\t(dataset = %s AND LOWER(scientificname) = %s) OR\n" % (
                    vnapi.encode_b64_for_psql(dataset_filter),
                    vnapi.encode_b64_for_psql(name.lower()),
                )

            sql_statements += "\tFALSE\n\n"

        # Render template.
        self.render_template('masterlist.html', {
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION,

            'message': message,

            'datasets_data': datasets_data,
            'dataset_filter': dataset_filter,
            'species': species,
            'species_sorted': scnames,

            'diff_names_count': diff_names_count,
            'diff_names_added': names_added,
            'diff_names_deleted': names_deleted,
            'diff_sql_statements': sql_statements
# ,

            # 'sql_add_to_master_list': sql_add_to_master_list,

        })




# Handle bulk uploads. The plan is to see if we can do this mostly in browser,
# using the following flow:
#   - no arguments: provide a form to upload a list of names.
#   - POST names: a list of names to have vernacular names added.
#   - POST names, langs, vernacularnames: preview data before import.
class BulkImportHandler(BaseHandler):
    def get(self):
        self.post()

    def post(self):
        self.response.headers['Content-type'] = 'text/html'
 
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Any input dataset name?
        input_dataset = self.request.get('input_dataset')
        if not input_dataset:
            input_dataset = 'New dataset uploaded on ' + time.strftime("%x %X %Z", time.gmtime())

        # Any names?
        all_names = self.request.get('scnames')
        scnames = []
        if all_names != '':
            scnames = filter(lambda x: x != '', re.split('\s*[\r\n]+\s*', all_names))

        # Any message?
        message = self.request.get('message')

        # Check for presence in master list.
        master_list = vnapi.getMasterList()
        master_list_lc = set(map(lambda x: x.lower(), master_list))
        
        scnames_not_in_master_list = filter(lambda x: (x.lower() not in master_list_lc), scnames)
        sql_add_to_master_list = "INSERT INTO %s (dataset, scientificname) VALUES\n\t%s" % (
            access.MASTER_LIST, ",\n\t".join(map(lambda scname: "('" + input_dataset.replace("'", "''") + "', '" + scname.replace("'", "''") + "')", scnames_not_in_master_list))
        )

        # Retrieve list of sources.
        all_sources = self.request.get('sources')
        manual_change = 'Manual changes on ' + time.strftime("%B %d, %Y", time.gmtime())
        sources = list(set(filter(lambda x: x != '' and x != manual_change, re.split("\\s*[\r\n]+\\s*", all_sources))))

        # Source priority
        source_priority = self.request.get_range('source_priority', vnapi.PRIORITY_MIN, vnapi.PRIORITY_MAX, vnapi.PRIORITY_DEFAULT)

        # This needs to go on top as it should be the default.
        sources.insert(0, manual_change) 

        # Read in any vernacular names.
        vnames_args = filter(lambda x: x.startswith('vname_'), self.request.arguments())
        vnames = [dict() for i in range(1, len(scnames) + 2)]
        vnames_source = [dict() for i in range(1, len(scnames) + 2)]
        for vname_arg in vnames_args:
            match = re.match(r"^vname_(\d+)_(\w+?)(_source|_in_nomdb)?$", vname_arg)
            if match:
                loop_index = int(match.group(1))
                lang = match.group(2)
                source_str = match.group(3)

                # Ignore 'vname_\d+_\w+_source'
                if source_str is None:
                    vname = self.request.get(vname_arg)
                    source = self.request.get(vname_arg + "_source")

                    # print("vname = " + vname + ", source = " + source + ".")

                    if vnames[loop_index] == 0:
                        vnames[loop_index] = {}

                    # Ignore any names which are identical to the name as in
                    # NomDB.
                    vname_in_nomdb = self.request.get(vname_arg + "_in_nomdb")
                    if vname_in_nomdb != '' and vname_in_nomdb == vname:
                        continue

                    if vname != '':
                        print(("scnames[" + str(loop_index - 1) + "] = " + scnames[loop_index - 1] + " => vnames[" + str(loop_index) + "][" + lang + "] = '" + vname + "'").encode('utf8'))
                        vnames[loop_index][lang] = vname.strip()
                        vnames_source[loop_index][lang] = source.strip()

        # Some variables for all entries.
        added_by = user.nickname()

        debug_save = ""
        if self.request.get('save') != "":
            # We need to save this and then redirect to the list view on this dataset. 
            # For now, we'll indicate what's going on in msg.
            entries = []

            save_errors = []

            debug_save = "<table border='1'>\n"
            for loop_index in range(1, len(scnames) + 1):
                # print("loop_index = " + str(loop_index) + ": " + str(vnames[loop_index]))

                for lang in vnames[loop_index]:
                    source = ""

                    if lang in vnames_source[loop_index]:
                        source = vnames_source[loop_index][lang]

                    # print("source = '" + source + "'")

                    if source is None or source == "":
                        save_errors.append("No source provided for '" + scnames[loop_index - 1] + "', cancelling.")
                        break

                    debug_save += "<tr><td>" + scnames[loop_index - 1] + "</td><td>" + lang + "</td><td>" + vnames[loop_index][lang] + "</td><td>" + source + "</td></tr>\n"

                    entries.append("(" + 
                        vnapi.encode_b64_for_psql(added_by) + ", " + 
                        vnapi.encode_b64_for_psql(scnames[loop_index - 1]) + ", " +
                            # loop_index - 1, since loop_index is 1-based (as it comes from the template)
                            # but the index on scnames is 0-based.
                        vnapi.encode_b64_for_psql(lang) + ", " + 
                        vnapi.encode_b64_for_psql(vnames[loop_index][lang]) + ", " +
                        vnapi.encode_b64_for_psql(source) + ", " + 
                        "'" + SOURCE_URL + "', " + str(source_priority) +
                        ")")

            debug_save += "</table>\n"

            print("um: " + str(len(save_errors)) + " of " + str(len(scnames)) + ".")

            if len(save_errors) > 0:
                message = "<strong>Error:</strong>" + "<br>".join(save_errors)
            else:
                # Write all the entries into CartoDB.
                # TODO: chunk this so we can add huge datasets.

                # Synthesize SQL
                sql = "INSERT INTO %s (added_by, scname, lang, cmname, source, source_url, source_priority) VALUES %s"
                sql_query = sql % (
                    access.ALL_NAMES_TABLE,
                    ", ".join(entries)
                )

                # Make it so.
                response = urlfetch.fetch(access.CDB_URL,
                    payload=urllib.urlencode(
                        dict(
                            q = sql_query,
                            api_key = access.CARTODB_API_KEY
                        )),
                    method=urlfetch.POST,
                    headers={'Content-type': 'application/x-www-form-urlencoded'},
                    deadline=vnapi.DEADLINE_FETCH
                )

                if response.status_code != 200:
                    message = "Error: server returned error " + str(response.status_code) + ": " + response.content
                    print("Error: server returned error " + str(response.status_code) + " on SQL '" + sql_query + "': " + response.content)

                else:
                    message = str(len(entries)) + " entries added to dataset '" + input_dataset + "'."

                    # Redirect to the main page.
                    self.redirect("/list?" + urllib.urlencode(dict(
                        msg = message,
                        dataset = input_dataset
                    )))

        # So far, we've processed all user input. Let's fill in the gaps with
        # data that already exists in the system. To simplify this query and
        # save me coding time, we'll retrieve *all* best match names.
        names_in_nomdb = dict()
        vnames_in_nomdb = [dict() for i in range(1, len(scnames) + 2)]
        if len(scnames) > 0:
            names_in_nomdb = vnnames.getVernacularNames(scnames,
                languages.language_names_list,
                flag_no_higher = False,
                flag_no_memoize = False
            )

        for loop_index in range(1, len(scnames) + 1):
            scname = scnames[loop_index - 1]
            for lang in languages.language_names_list:
                if lang not in vnames[loop_index]:
                    vname = names_in_nomdb[scname][lang]
                    vnames[loop_index][lang] = vname.cmname
                    source = "; ".join(sorted(set(vname.sources)))
                    if source not in sources and source != '':
                        sources.append(source)
                    vnames_source[loop_index][lang] = source

                    # Store in vnames_in_nomdb so we know if they've been edited.
                    vnames_in_nomdb[loop_index][lang] = vname.cmname

        # If this is a get request, we can only be in display-first-page mode.
        # So display first page and quit.
        self.render_template('import.html', {
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION,

            'debug_save': debug_save,

            'url_master_list': "https://mol.cartodb.com/tables/" + access.MASTER_LIST,
            'sql_add_to_master_list': sql_add_to_master_list,

            'message': message,

            'scnames': scnames,
            'scnames_not_in_master_list': scnames_not_in_master_list,
            'source_priority': source_priority,
            'input_dataset' : input_dataset,
            'sources': sources,
            'vnames': vnames,
            'vnames_in_nomdb': vnames_in_nomdb,
            'vnames_source': vnames_source,

            # For higher taxonomy.
            'names_in_nomdb': names_in_nomdb
        })

# GeneraHandler view.
class GeneraHandler(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Get list of higher taxonomy, limited to those referred from
        # scientific names in the master list.
        genera_sql = """
            SELECT 
                LOWER(split_part(scientificname, ' ', 1)) AS genus,
                ARRAY_AGG(DISTINCT family) AS family,
                COUNT(DISTINCT LOWER(scientificname)) AS count_species,
                ARRAY_AGG(DISTINCT dataset ORDER BY dataset ASC) AS datasets
            FROM %s AS master
            GROUP BY LOWER(split_part(scientificname, ' ', 1)), family
            ORDER BY genus ASC, family ASC NULLS FIRST
        """ % (
            access.MASTER_LIST
        )
  
        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(
                dict(
                    q = genera_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        all_species = []
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + genera_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            all_species = results['rows']

        # http://stackoverflow.com/a/408281/27310
        def flatten(iter_list):
            return list(item for iter_ in iter_list for item in iter_)

        missing_genera = filter(lambda x: x['family'] is None, all_species)
        genera = filter(lambda x: x['family'] is not None, all_species)
        all_names = filter(lambda x: x is not None, map(lambda x: x['family'], all_species))

        # Render recent changes.
        self.render_template('genera.html', {
            'message': message,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'missing_genera': missing_genera,
            'genera': genera,
            'vnames': vnnames.getVernacularNames(
                flatten(all_names), 
                languages.language_names_list, 
                flag_no_higher = True, 
                flag_no_memoize = True, 
                flag_lookup_genera = False, 
                flag_format_cmnames = True),
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.VNEDITOR_VERSION
        })
   
# HemihomonymHandler view: displays and warns about hemihomonyms.
class HemihomonymHandler(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Get list of higher taxonomy, limited to those referred from
        # scientific names in the master list.
        hemihomonym_sql = """
            SELECT 
                LOWER(genus) AS genus, 
                array_agg(DISTINCT scientificname) AS scnames, 
                array_agg(DISTINCT family) AS families,
                array_agg(DISTINCT dataset) AS datasets
            FROM %s
            GROUP BY genus 
            HAVING COUNT(DISTINCT family) > 1
            ORDER BY genus ASC NULLS FIRST
        """ % (
            access.MASTER_LIST
        )
  
        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(
                dict(
                    q = hemihomonym_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        hemihomonyms = []
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + hemihomonym_sql+ "'), server returned error " + str(response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            hemihomonyms = results['rows']

        scnames = set()
        for row in hemihomonyms:
            scnames.update(row['scnames'])

        # http://stackoverflow.com/a/408281/27310
        def flatten(iter_list):
            return list(item for iter_ in iter_list for item in iter_)

        # Render recent changes.
        self.render_template('hemihomonyms.html', {
            'message': message,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,

            'scnames': scnames,
            'hemihomonyms': vnapi.groupBy(hemihomonyms, 'genus'),
            'vnames': vnnames.getVernacularNames(
                scnames,
                languages.language_names_list, 
                flag_no_higher = True, 
                flag_no_memoize = True, 
                flag_lookup_genera = True, 
                flag_format_cmnames = True),

            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,

            'vneditor_version': version.VNEDITOR_VERSION
        }) 

# Display higher taxonomy view.
class HigherTaxonomyHandler(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'
        
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Get list of higher taxonomy, limited to those referred from
        # scientific names in the master list.
        higher_taxonomy_sql = """
            SELECT 
                CASE    WHEN tax_class IS NULL THEN '_null'
                        WHEN tax_class = '' THEN '_blank'
                        ELSE LOWER(tax_class) 
                END AS tax_class_lc,

                CASE    WHEN tax_order IS NULL THEN '_null'
                        WHEN tax_order = '' THEN '_blank'
                        ELSE LOWER(tax_order) 
                END AS tax_order_lc, 

                CASE    WHEN tax_family IS NULL THEN '_null'
                        WHEN tax_family = '' THEN '_blank'
                        ELSE LOWER(tax_family)
                END AS tax_family_lc,

                COUNT(DISTINCT LOWER(scname)) AS count_species,
                COUNT(*) OVER() AS total_count
            FROM %s RIGHT JOIN %s ON LOWER(scientificname) = LOWER(scname)
            GROUP BY tax_class_lc, tax_order_lc, tax_family_lc
            ORDER BY tax_class_lc, tax_order_lc, tax_family_lc
        """ %  (
            access.ALL_NAMES_TABLE,
            access.MASTER_LIST
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(
                dict(
                    q = higher_taxonomy_sql
                )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=vnapi.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        higher_taxonomy = []
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + higher_taxonomy_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            higher_taxonomy = results['rows']
            if len(higher_taxonomy) > 0:
                total_count = higher_taxonomy[0]['total_count']

        higher_taxonomy_tree = {}

        classes = vnapi.groupBy(higher_taxonomy, 'tax_class_lc')
        for tax_class in classes:
            orders = vnapi.groupBy(classes[tax_class], 'tax_order_lc')
            for tax_order in orders:
                families = vnapi.groupBy(orders[tax_order], 'tax_family_lc')
                for tax_family in families:
                    if not tax_class in higher_taxonomy_tree:
                        higher_taxonomy_tree[tax_class] = {}
                    if not tax_order in higher_taxonomy_tree[tax_class]:
                        higher_taxonomy_tree[tax_class][tax_order] = {}

                    higher_taxonomy_tree[tax_class][tax_order][tax_family] = families[tax_family]

        tax_class = sorted(set(map(lambda x: x['tax_class_lc'], higher_taxonomy)))
        tax_order = sorted(set(map(lambda x: x['tax_order_lc'], higher_taxonomy)))
        tax_family = sorted(set(map(lambda x: x['tax_family_lc'], higher_taxonomy)))

        all_names = filter(lambda x: x is not None and x != '', set(tax_class) | set(tax_order) | set(tax_family))

        # Render recent changes.
        self.render_template('taxonomy.html', {
            'message': message,
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'vnames': vnnames.getVernacularNames(all_names, languages.language_names_list, flag_no_higher = True, flag_no_memoize = False, flag_lookup_genera = False, flag_format_cmnames = True),
            'tax_class': tax_class,
            'tax_order': tax_order,
            'tax_family': tax_family,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'total_count': total_count,
            'higher_taxonomy': higher_taxonomy,
            'higher_taxonomy_tree': higher_taxonomy_tree,
            'vneditor_version': version.VNEDITOR_VERSION
        })



# Return a list of recent changes, and allow some to be deleted.
class RecentChangesHandler(BaseHandler):
    def get(self):
        self.response.headers['Content-type'] = 'text/html'
        
        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url('/')

        if user is None:
            return

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Is there an offset?
        offset = self.request.get_range('offset', 0, default=0)
        display_count = 100

        # Synthesize SQL
        recent_sql = ("""SELECT cartodb_id, scname, lang, cmname, source, url, source_priority, tax_class, tax_order, tax_family, added_by, created_at, updated_at,
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
            message += "<br><strong>Error</strong>: query ('" + recent_sql + "'), server returned error " + str(response.status_code) + ": " + response.content
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
            # sql_having.append("NOT " + vnapi.encode_b64_for_psql(lang.lower()) + " = ANY(array_agg(LOWER(lang)))")
            sql_having.append("NOT array_agg(DISTINCT LOWER(lang)) = ARRAY[" + vnapi.encode_b64_for_psql(lang.lower()) + "]")
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

        if user is None:
            return

        # Message?
        message = self.request.get('msg')

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
        display_count = int(use_last_or_default("display", LISTVIEWHANDLER_DEFAULT_ROWS))

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
        order_by = "ORDER BY scientificname COLLATE \"POSIX\" ASC"
            # We collate POSIX because otherwise spaces get ignored while
            # sorting in CartoDB.
        results['search_criteria'].append("sorted by ascending scientific name")

        limit_offset = "LIMIT %d OFFSET %d" % (
            display_count,
            offset
        )

        search_criteria = ", ".join(results['search_criteria'])

        # Put all the pieces of the SELECT statement together.
        list_sql = """SELECT
            %s
            FROM %s LEFT JOIN %s ON (LOWER(scname) = LOWER(scientificname))
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
        if response.status_code != 200:
            message += "\n<p><strong>Error</strong>: query ('" + list_sql + "'), server returned error " + str(response.status_code) + ": " + response.content + "</p>"
            results = {"rows": []}
        else:
            if FLAG_DEBUG:
                message += "\n<p>DEBUG: '" + list_sql + "'</p>"
            results = json.loads(response.content)

        name_list = map(lambda x: x['scientificname'], results['rows'])
        genera_list = sorted(set(map(lambda x: x['scientificname'].partition(' ')[0], results['rows'])))
        total_count = 0
        if FLAG_LIST_DISPLAY_COUNT and len(results['rows']) > 0:
            total_count = results['rows'][0]['total_count']

        vnames = vnnames.getVernacularNames(name_list, languages.language_names_list, flag_no_higher=True, flag_no_memoize=True, flag_all_results=False, flag_lookup_genera=True, flag_format_cmnames=True)

        self.render_template('list.html', {
            'vneditor_version': version.VNEDITOR_VERSION,
            'user_url': user_url,
            'user_name': user_name,
            'language_names_list': languages.language_names_list,
            'language_names': languages.language_names,
            'datasets_data': vnapi.getDatasets(),
            'selected_datasets': set(self.request.get_all('dataset')),
            'selected_blank_langs': set(self.request.get_all('blank_lang')),
            'message': message,
            'search_criteria': search_criteria,
            'name_list': name_list,
            'genera_list': genera_list,
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
    ('/taxonomy', HigherTaxonomyHandler),
    ('/genera', GeneraHandler),
    ('/hemihomonyms', HemihomonymHandler),
    ('/sources', SourcesHandler),
    ('/coverage', CoverageViewHandler),
    ('/import', BulkImportHandler),
    ('/masterlist', MasterListHandler),
    ('/generate/taxonomy_translations', GenerateTaxonomyTranslations)
], debug=not PROD)
