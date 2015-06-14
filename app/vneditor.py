# vim: set fileencoding=utf-8 :

import urllib
import re
import time
import os
import json
import logging
import inspect
import datetime

import webapp2

from google.appengine.api import users, urlfetch
from google.appengine.ext import ereporter
import jinja2
import access
import nomdb.version
from nomdb import masterlist, names, config, common, languages, version


# User interface configuration

""" What is the base URL for this app? """
BASE_URL = '/taxonomy/names'

""" Display DEBUG information? This is only used once. """
FLAG_DEBUG = False

""" Display the total count in $BASE_URL/list: expensive, but useful. """
FLAG_LIST_DISPLAY_COUNT = True

""" How many rows to display in $BASE_URL/list by default. """
LISTVIEWHANDLER_DEFAULT_ROWS = 500

""" Sources with fewer vname entries than this are considered to be individual imports;
greater than this are bulk imports."""
INDIVIDUAL_IMPORT_LIMIT = 100

""" What the 'source' should be when adding new rows. """
SOURCE_URL = "https://github.com/gaurav/vernacular-names"

#
# INITIALIZATION
#
urlfetch.set_default_fetch_deadline(config.DEADLINE_FETCH)
ereporter.register_logger()

# Check whether we're in production mode (PROD = True) or not.
PROD = True
if 'SERVER_SOFTWARE' in os.environ:
    PROD = not os.environ['SERVER_SOFTWARE'].startswith('Development')

# Set up the Jinja templating environment
JINJA_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)),
    extensions=['jinja2.ext.autoescape'],
    autoescape=True
)
JINJA_ENV.filters['url_to_base'] = lambda x: BASE_URL + x

#
# BaseHandler
#
class BaseHandler(webapp2.RequestHandler):
    """ The BaseHandler sets up some basic routines that all pages use."""

    def render_template(self, f, template_args):
        """render_template renders a Jinja2 template from the 'templates' dir,
        using the template arguments provided."""
        path = os.path.join("templates", f)
        template = JINJA_ENV.get_template(path)
        self.response.out.write(template.render(template_args))

    def check_user(self):
        """check_user checks to see if the user should be allowed to access
        this application (are they a signed in Google user who is an administrator
        on this project?). If not, it redirects them to /page/private"""
        user = users.get_current_user()
        if not user:
            self.redirect(users.create_login_url(self.request.uri))

        if not users.is_current_user_admin():
            self.redirect(BASE_URL + '/page/private')

        return user

#
# STATIC PAGE HANDLER:
#   - /page/private: error message asking user to log in.
#
class StaticPages(BaseHandler):
    """ The StaticPages handler handles our static pages by providing them
    with a set of default variables."""

    def __init__(self, request, response):
        BaseHandler.__init__(self, request, response)

        """ Right now, there's only $BASE_URL/page/private. """
        self.template_mappings = {
            BASE_URL + '/page/private': 'private.html'
        }
        self.initialize(request, response)

    def get(self):
        """ Retrieve the static page and render it, or provide a 404 error. """
        self.response.headers['Content-Type'] = 'text/html'

        path = self.request.path.lower()
        if path in self.template_mappings:
            self.render_template(self.template_mappings[path], {
                'login_url': users.create_login_url(BASE_URL),
                'logout_url': users.create_logout_url(BASE_URL),
                'vneditor_version': version.NOMDB_VERSION
            })
        else:
            self.response.status = 404


#
# MAIN PAGE HANDLER: /
#
class MainPage(BaseHandler):
    def get(self):
        """ Display a welcome screen.
        """
        self.response.headers['Content-type'] = 'text/html'

        # Set up user details.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        # Allow not-logged-in code to run.
        if user is None:
            return

        # Render the main template.
        self.render_template('main.html', {
            'message': self.request.get('msg'),
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'vneditor_version': version.NOMDB_VERSION
        })

#
# SEARCH PAGE HANDLER: /search
#
class SearchPage(BaseHandler):
    def get(self):
        """ Display the search results and allows individual entries to be edited.

        For historical reasons, this page does both search and edit, and does it
        awfully. However, I'm not sure too many people use this functionality much
        except for basic editing right now, so I'm not going to clean it until we
        fully understand what this copmoenent should look like.
        """
        # TODO https://github.com/MapofLife/vernacular-names/issues/59

        self.response.headers['Content-type'] = 'text/html'

        # Set up user details.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        # Allow not-logged-in code to run.
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
            search_results = masterlist.search_for_name(current_search)
            search_results_scnames = sorted(search_results.keys())

        # Check for dataset_filter
        dataset_filter = self.request.get('dataset')
        if dataset_filter != '':
            if current_search != '':
                # If there is a search, filter it using dataset_filter.
                search_results_scnames = filter(
                    lambda scname: masterlist.dataset_contains_name(dataset_filter, scname),
                    search_results_scnames
                )
            else:
                # If not, search by dataset.
                search_results_scnames = masterlist.get_dataset_names(dataset_filter)

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
            lookup_results = names.get_detailed_vname(lookup_search)

            # Summarize higher taxonomy.
            tax_family = lookup_results['tax_family']
            tax_order = lookup_results['tax_order']
            tax_class = lookup_results['tax_class']

        # Render the main template.
        self.render_template('search.html', {
            'message': self.request.get('msg'),
            'dataset_filter': dataset_filter,
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
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
            'vneditor_version': version.NOMDB_VERSION
        })

#
# DELETE NAME BY CartoDBID HANDLER: /delete/cartodb_id
#
class DeleteNameByCDBIDHandler(BaseHandler):
    """ This handler will delete a row using its CartoDB identifier. """

    def post(self):
        """Delete name by $cartodb_id and redirect to /recent
        """

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
        response = urlfetch.fetch(access.CDB_URL + "?" + urllib.urlencode(
            dict(
                q=sql_query,
                api_key=access.CARTODB_API_KEY
            )
        ))

        if response.status_code != 200:
            message = "Error: server returned error " + str(response.status_code) + ": " + response.content
        else:
            message = "Change %d deleted successfully." % cartodb_id

        # Redirect to the recent changes page, which is the only person using this service right now.
        self.redirect(BASE_URL + "/recent?" + urllib.urlencode(dict(
            msg=message,
        )))


#
# ADD NAME HANDLER: /add/name
#
class AddNameHandler(BaseHandler):
    """This handler will add a new name to the names table in CartoDB."""

    def post(self):
        """Save new name from $name_to_add in $lang with $source, and redirect to /"""

        # Fail without login.
        current_user = self.check_user()
        if current_user is None:
            return

        # Retrieve state. We use this for the final redirect and to figure out which name to add to.
        search = self.request.get('search')
        lookup = self.request.get('lookup')

        # Retrieve name to add
        scname = lookup
        cmname = self.request.get('name_to_add')
        lang = self.request.get('lang').lower()
        source = self.request.get('source')
        source_priority = self.request.get_range('priority', config.PRIORITY_MIN, config.PRIORITY_MAX,
                                                 config.PRIORITY_DEFAULT_APP)

        tax_class = self.request.get('tax_class')
        tax_order = self.request.get('tax_order')
        tax_family = self.request.get('tax_family')

        # Check that the name being added is sensible.
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
            added_by_b64 = nomdb.common.encode_b64_for_psql(added_by)
            scname_b64 = nomdb.common.encode_b64_for_psql(scname)
            cmname_b64 = nomdb.common.encode_b64_for_psql(cmname)
            lang_b64 = nomdb.common.encode_b64_for_psql(lang)
            source_b64 = nomdb.common.encode_b64_for_psql(source)

            tax_class_b64 = nomdb.common.encode_b64_for_psql(tax_class.strip().lower())
            tax_order_b64 = nomdb.common.encode_b64_for_psql(tax_order.strip().lower())
            tax_family_b64 = nomdb.common.encode_b64_for_psql(tax_family.strip().lower())

            # Synthesize SQL
            sql = "INSERT INTO %s (added_by, scname, lang, cmname, url, source, source_url, source_priority, tax_class, tax_order, tax_family) " \
                  "VALUES (%s, %s, %s, %s, NULL, %s, '" + SOURCE_URL + "', %d, %s, %s, %s);"
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
            response = urlfetch.fetch(access.CDB_URL + "?" + urllib.urlencode(
                dict(
                    q=sql_query,
                    api_key=access.CARTODB_API_KEY
                )
            ))

            if response.status_code != 200:
                message = "Error: server returned error " + str(response.status_code) + ": " + response.content
            else:
                message = "Name added to language '" + lang + "'."

        # Redirect to the main page.
        self.redirect(BASE_URL + "/search?" + urllib.urlencode(dict(
            msg=message,
            search=search,
            lookup=lookup
        )) + "#lang-" + lang)


#
# COVERAGE VIEW HANDLER: /coverage
#
class CoverageViewHandler(BaseHandler):
    """Display a summary of the coverage by dataset and language."""

    def get(self):
        """ Display /coverage results. """
        self.response.headers['Content-type'] = 'text/html'

        # Check that a user is logged in; otherwise, bail out.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        if user is None:
            return

        # Pagination
        offset = int(self.request.get('offset', 0))
        default_display = 50
        display = int(self.request.get('display', default_display))

        langs = languages.language_names_list

        # Display 'display', offset by offset.
        all_datasets = masterlist.get_datasets()
        datasets = all_datasets[offset:offset + display]

        dataset_names = map(lambda x: x['dataset'], all_datasets)
        coverage = masterlist.get_dataset_coverage(dataset_names, langs)
        datasets_count = coverage['num_species']
        datasets_coverage = coverage['coverage']

        # Render coverage template.
        self.render_template('coverage.html', {
            'vneditor_version': version.NOMDB_VERSION,
            'user_url': user_url,
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
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

#
# SOURCES HANDLER: /sources
#
class SourcesHandler(BaseHandler):
    """Lists the sources and their priorities and to change them."""

    DEFAULT_DISPLAY_COUNT = 100

    def post(self):
        """ Handle changing the name, URL or priority of an entire source at once.
        We only handle changing EITHER the name OR the priority, not both at the same time.
        """

        # Make sure a user is logged in.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        if user is None:
            return

        # Set up error msg.
        message = ""

        # Retrieve offset/display of the /sources page we should redirect to.
        offset = self.request.get_range('offset', 0, default=0)
        display_count = self.request.get_range('display', 0, default=SourcesHandler.DEFAULT_DISPLAY_COUNT)

        # Retrieve source to modify
        source = self.request.get('source')

        if source == '':
            message = "No source provided; nothing to edit."

        elif self.request.get('source_priority'):
            source_priority = self.request.get_range('source_priority', config.PRIORITY_MIN, config.PRIORITY_MAX, config.PRIORITY_MIN)

            if config.PRIORITY_MIN <= source_priority <= config.PRIORITY_MAX:
                # Synthesize SQL
                sql = "UPDATE %s SET source_priority = %d WHERE source=%s"
                sql_query = sql % (
                    access.ALL_NAMES_TABLE,
                    source_priority,
                    nomdb.common.encode_b64_for_psql(source)
                )

                # Make it so.
                response = urlfetch.fetch(access.CDB_URL + "?" +  urllib.urlencode(
                    dict(
                        q=sql_query,
                        api_key=access.CARTODB_API_KEY
                    )
                ))

                if response.status_code != 200:
                    message = "Error: server returned error " + str(response.status_code) + ": " + response.content
                else:
                    message = "Source priority modified to %d." % source_priority
        elif self.request.get('source_new_name'):
            source_new_name = self.request.get('source_new_name')
            source_url = self.request.get('source_url')

            if source_new_name == '':
                # Blank names will not be accepted.

                logging.warning("User tried to change source to '%s', which is invalid." % source_new_name)
                message = "Could not parse new source name, please try another."

            else:
                # Synthesize SQL
                sql = "UPDATE %s SET source=%s, source_url=%s WHERE source=%s"
                sql_query = sql % (
                    access.ALL_NAMES_TABLE,
                    nomdb.common.encode_b64_for_psql(source_new_name),
                    nomdb.common.encode_b64_for_psql(source_url),
                    nomdb.common.encode_b64_for_psql(source)
                )

                # Make it so.
                response = urlfetch.fetch(access.CDB_URL + "?" +  urllib.urlencode(
                    dict(
                        q=sql_query,
                        api_key=access.CARTODB_API_KEY
                    )
                ))

                if response.status_code != 200:
                    logging.error("SQL query '%s' failed: %s" % (sql_query, response.content))
                    message = "Error: server returned error " + str(response.status_code) + ": " + response.content
                else:
                    message = "Source '%s' renamed to '%s' and URL modified to '%s' successfully." % (source, source_new_name, source_url)
        else:
            message = "Neither source priority nor source new name was provided."

        # Redirect to the main page.
        self.redirect(BASE_URL + "/sources?" + urllib.urlencode(dict(
            msg=message,
            display=display_count,
            offset=offset
        )))

    def get(self):
        """ Display list of sources currently being used. """

        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        if user is None:
            return

        # Is there an offset?
        offset = self.request.get_range('offset', 0, default=0)
        display_count = self.request.get_range('display', 0, default=SourcesHandler.DEFAULT_DISPLAY_COUNT)

        # Is there a message?
        message = self.request.get('msg')
        if not message:
            message = ""

        # Synthesize SQL
        source_sql = ("""SELECT 
            source,
            source_url,
            COUNT(*) OVER () AS total_count,
            COUNT(*) AS vname_count,
            ARRAY_AGG(TO_CHAR(created_at, 'YYYY-MM')) AS agg_created_at,
            array_agg(source_priority) AS agg_source_priority,
            array_agg(lang) AS agg_lang
            FROM %s 
            GROUP BY source, source_url
            ORDER BY vname_count DESC, source ASC
            LIMIT %d OFFSET %d
        """) % (
            access.ALL_NAMES_TABLE,
            display_count,
            offset
        )

        # Make it so.
        response = urlfetch.fetch(
            access.CDB_URL,
            payload=urllib.urlencode(
                {'q': source_sql}),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=config.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + source_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content
            sources = []
        else:
            results = json.loads(response.content)
            sources = results['rows']
            if len(sources) > 0:
                total_count = sources[0]['total_count']

            # Distinctify and reformat some columns.
            for row in sources:
                # logging.info("Processing row: " + str(row))

                row['vname_count_formatted'] = '{:,}'.format(int(row['vname_count']))
                row['agg_lang'] = sorted(set(row['agg_lang']))
                row['agg_source_priority'] = sorted(set(row['agg_source_priority']))

                # Produce a list of continguous date sequences.
                # i.e. something like ["August 2014", "February to March 2015", "June 2015"]
                sorted_dates = (map(lambda x: datetime.datetime.strptime(x, "%Y-%m"), sorted(set(row['agg_created_at']))))

                row['dates_added'] = []

                prev_group = []

                def format_dateset(prev_group):
                    if len(prev_group) == 0:
                        return []
                    elif len(prev_group) == 1:
                        return prev_group[0].strftime("%B %Y")
                    else:
                        return prev_group[0].strftime("%B %Y") + " to " + prev_group[-1].strftime("%B %Y")

                # logging.info("Sorted dates for '" + row['source'] + "': " + str(sorted_dates))
                for date in sorted_dates:
                    # Is 'date' part of the previous group?
                    if len(prev_group) == 0:
                        prev_group.append(date)
                    else:
                        # logging.info("Date '" + str(date) + "' - '" + str(prev_group[-1]) + "' <=> 4 days")
                        if (date - prev_group[-1]) < datetime.timedelta(weeks = 5):
                            prev_group.append(date)
                        else:
                            row['dates_added'].append(format_dateset(prev_group))
                            prev_group = [date]

                if len(prev_group) > 0:
                    row['dates_added'].append(format_dateset(prev_group))


        # There are two kinds of sources:
        #   1. Anything <= INDIVIDUAL_IMPORT_LIMIT is an individual import from the source.
        #       These should be grouped by prefix.
        #   2. Anything > INDIVIDUAL_IMPORT_LIMIT is a bulk import, and should be displayed separately.
        #individual_imports = filter(lambda x: int(x['vname_count']) <= INDIVIDUAL_IMPORT_LIMIT, sources)
        #bulk_imports = filter(lambda x: int(x['vname_count']) > INDIVIDUAL_IMPORT_LIMIT, sources)

        # Render sources.
        self.render_template('sources.html', {
            'message': message,
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,

            'offset': offset,
            'display_count': display_count,

            'total_count': total_count,
            'sources': sources,
            #'bulk_imports': bulk_imports,
            #'individual_imports': individual_imports,

            'vneditor_version': version.NOMDB_VERSION
        })

#
# MASTER LIST HANDLER: /masterlist
#
class MasterListHandler(BaseHandler):
    """ Display the list of species in the master list, or compare a list you have with the list we have at
        the moment. """

    def get(self):
        # We do the same thing, so we might as well handle it all in POST.
        self.post()

    def post(self):
        """ Compare the master list with a list of scnames provided, or
        just display the list by itself.
        """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        if user is None:
            return

        # Any message?
        message = self.request.get('message') # TODO: standardize to 'msg'

        # Retrieve master list.
        dataset_filter = self.request.get('dataset')
        datasets_data = masterlist.get_dataset_counts()
        if dataset_filter == '':
            datasets = map(lambda x: x['dataset'], datasets_data)
        else:
            datasets = {dataset_filter}

        species = dict()
        for dataset in datasets:
            scnames = masterlist.get_dataset_names(dataset)

            for scname in scnames:
                scname = scname.lower()
                if scname in species:
                    species[scname]['datasets'].add(dataset)
                else:
                    species[scname] = dict(
                        datasets={dataset}
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
                    nomdb.common.encode_b64_for_psql(dataset_filter),
                    nomdb.common.encode_b64_for_psql(name.capitalize()),
                )

            sql_statements = sql_statements.rstrip(",\n")
            sql_statements += "\n\n"

        if len(names_deleted) > 0:
            sql_statements += "DELETE FROM %s WHERE\n" % (
                access.MASTER_LIST
            )
            for name in names_deleted:
                sql_statements += "\t(dataset = %s AND LOWER(scientificname) = %s) OR\n" % (
                    nomdb.common.encode_b64_for_psql(dataset_filter),
                    nomdb.common.encode_b64_for_psql(name.lower()),
                )

            sql_statements += "\tFALSE\n\n"

        # Render template.
        self.render_template('masterlist.html', {
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.NOMDB_VERSION,

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


#
# BULK IMPORT HANDLER: /import
#
class BulkImportHandler(BaseHandler):
    def get(self):
        # We don't have any GET things to do, so just redirect to POST.
        self.post()

    def post(self):
        """ Handle bulk uploads. The plan is to see if we can do this mostly in browser,
        using the following flow:
         - no arguments: provide a form to upload a list of names.
         - POST names: a list of names to have vernacular names added.
         - POST names, langs, vernacularnames: preview data before import.

        Once we're done, we'll save the names and redirect to /recent.
        """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
        master_list = masterlist.get_master_list()
        master_list_lc = set(map(lambda x: x.lower(), master_list))

        scnames_not_in_master_list = filter(lambda x: (x.lower() not in master_list_lc), scnames)
        sql_add_to_master_list = "INSERT INTO %s (dataset, scientificname) VALUES\n\t%s" % (
            access.MASTER_LIST, ",\n\t".join(
                map(lambda scname: "('" + input_dataset.replace("'", "''") + "', '" + scname.replace("'", "''") + "')",
                    scnames_not_in_master_list))
        )

        # Retrieve list of sources.
        all_sources = self.request.get('sources')
        manual_change = 'Manual changes on ' + time.strftime("%B %d, %Y", time.gmtime())
        sources = list(set(filter(lambda x: x != '' and x != manual_change, re.split("\\s*[\r\n]+\\s*", all_sources))))

        # Source priority
        source_priority = self.request.get_range('source_priority', config.PRIORITY_MIN,
                                                 config.PRIORITY_MAX, config.PRIORITY_DEFAULT)

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
                        logging.info((
                                  "scnames[" + str(loop_index - 1) + "] = " + scnames[loop_index - 1] + " => vnames[" + str(
                                      loop_index) + "][" + lang + "] = '" + vname + "'").encode('utf8'))
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

                    debug_save += "<tr><td>" + scnames[loop_index - 1] + "</td><td>" + lang + "</td><td>" + \
                                  vnames[loop_index][lang] + "</td><td>" + source + "</td></tr>\n"

                    entries.append("(" +
                                   nomdb.common.encode_b64_for_psql(added_by) + ", " +
                                   nomdb.common.encode_b64_for_psql(scnames[loop_index - 1]) + ", " +
                                   # loop_index - 1, since loop_index is 1-based (as it comes from the template)
                                   # but the index on scnames is 0-based.
                                   nomdb.common.encode_b64_for_psql(lang) + ", " +
                                   nomdb.common.encode_b64_for_psql(vnames[loop_index][lang]) + ", " +
                                   nomdb.common.encode_b64_for_psql(source) + ", " +
                                   "'" + SOURCE_URL + "', " + str(source_priority) +
                                   ")")

            debug_save += "</table>\n"

            logging.error("um: " + str(len(save_errors)) + " of " + str(len(scnames)) + ".")

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
                response = urlfetch.fetch(
                    access.CDB_URL,
                    payload=urllib.urlencode(
                        {'q': sql_query, 'api_key': access.CARTODB_API_KEY}
                    ),
                    method=urlfetch.POST,
                    headers={'Content-type': 'application/x-www-form-urlencoded'},
                    deadline=config.DEADLINE_FETCH
                )

                if response.status_code != 200:
                    message = "Error: server returned error " + str(response.status_code) + ": " + response.content
                    logging.error("Error: server returned error " + str(
                        response.status_code) + " on SQL '" + sql_query + "': " + response.content)

                else:
                    message = str(len(entries)) + " entries added to dataset '" + input_dataset + "'."

                    # Redirect to the main page.
                    self.redirect(BASE_URL + "/list?" + urllib.urlencode({'msg': message, 'dataset': input_dataset}))

        # So far, we've processed all user input. Let's fill in the gaps with
        # data that already exists in the system. To simplify this query and
        # save me coding time, we'll retrieve *all* best match names.
        names_in_nomdb = dict()
        vnames_in_nomdb = [dict() for i in range(1, len(scnames) + 2)]
        if len(scnames) > 0:
            names_in_nomdb = names.get_vnames(scnames)

        for loop_index in range(1, len(scnames) + 1):
            scname = scnames[loop_index - 1]
            for lang in languages.language_names_list:
                if lang not in vnames[loop_index]:
                    vname = names_in_nomdb[scname][lang]
                    if vname is not None:
                        vnames[loop_index][lang] = vname.vernacular_name
                        source = vname.source
                        if source not in sources and source != '':
                            sources.append(source)
                        vnames_source[loop_index][lang] = source

                        # Store in vnames_in_nomdb so we know if they've been edited.
                        vnames_in_nomdb[loop_index][lang] = vname.vernacular_name

        # If this is a get request, we can only be in display-first-page mode.
        # So display first page and quit.
        self.render_template('import.html', {
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.NOMDB_VERSION,

            'debug_save': debug_save,

            'url_master_list': "https://mol.cartodb.com/tables/" + access.MASTER_LIST,
#            'sql_add_to_master_list': sql_add_to_master_list,

            'message': message,

            'scnames': scnames,
            'scnames_not_in_master_list': scnames_not_in_master_list,
            'source_priority': source_priority,
            'input_dataset': input_dataset,
            'sources': sources,
            'vnames': vnames,
            'vnames_in_nomdb': vnames_in_nomdb,
            'vnames_source': vnames_source,

            # For higher taxonomy.
            'names_in_nomdb': names_in_nomdb
        })

#
# FAMILY HANDLER: /family
#
class FamilyHandler(BaseHandler):

    def get(self):
        """ Display list of family names. """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
        response = urlfetch.fetch(
            access.CDB_URL,
            payload=urllib.urlencode({'q': genera_sql}),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=config.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        all_species = []
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + genera_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content
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
        self.render_template('family.html', {
            'message': message,
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'missing_genera': missing_genera,
            'genera': genera,
            'vnames': names.get_vnames(flatten(all_names)),
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'vneditor_version': version.NOMDB_VERSION
        })

#
# HEMIHOMONYM HANDLER: /hemihomonyms
#
class HemihomonymHandler(BaseHandler):
    """HemihomonymHandler view: displays and warns about hemihomonyms."""

    def get(self):
        """List hemihomonyms."""
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
                                          q=hemihomonym_sql
                                      )),
                                  method=urlfetch.POST,
                                  headers={'Content-type': 'application/x-www-form-urlencoded'},
                                  deadline=config.DEADLINE_FETCH
                                  )

        # Retrieve results. Store the total count if there is one.
        hemihomonyms = []
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + hemihomonym_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content
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
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,

            'scnames': scnames,
            'hemihomonyms': nomdb.common.group_by(hemihomonyms, 'genus'),
            'vnames': names.get_vnames(scnames),
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,

            'vneditor_version': version.NOMDB_VERSION
        })

#
# HIGHER TAXONOMY HANDLER: /taxonomy
#
class HigherTaxonomyHandler(BaseHandler):
    """NomDB has information about higher taxonomy from its various sources. This allows it to generate consensus
    higher-taxonomy information. You can access that here."""

    def get(self):
        """ Retrieve a list of higher taxonomy for the species being queried."""
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
        """ % (
            access.ALL_NAMES_TABLE,
            access.MASTER_LIST
        )

        # Make it so.
        response = urlfetch.fetch(access.CDB_URL,
                                  payload=urllib.urlencode(
                                      dict(
                                          q=higher_taxonomy_sql
                                      )),
                                  method=urlfetch.POST,
                                  headers={'Content-type': 'application/x-www-form-urlencoded'},
                                  deadline=config.DEADLINE_FETCH
                                  )

        # Retrieve results. Store the total count if there is one.
        higher_taxonomy = []
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + higher_taxonomy_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            higher_taxonomy = results['rows']
            if len(higher_taxonomy) > 0:
                total_count = higher_taxonomy[0]['total_count']

        higher_taxonomy_tree = {}

        classes = nomdb.common.group_by(higher_taxonomy, 'tax_class_lc')
        for tax_class in classes:
            orders = nomdb.common.group_by(classes[tax_class], 'tax_order_lc')
            for tax_order in orders:
                families = nomdb.common.group_by(orders[tax_order], 'tax_family_lc')
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
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'vnames': names.get_vnames(all_names),
            'tax_class': tax_class,
            'tax_order': tax_order,
            'tax_family': tax_family,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'total_count': total_count,
            'higher_taxonomy': higher_taxonomy,
            'higher_taxonomy_tree': higher_taxonomy_tree,
            'vneditor_version': version.NOMDB_VERSION
        })

#
# RECENT CHANGES HANDLER: /recent
#
class RecentChangesHandler(BaseHandler):
    """ Return a list of recent changes, and allow some to be deleted. """

    def get(self):
        """ Return a list of recent changes, and allow some to be deleted. """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
        response = urlfetch.fetch(
            access.CDB_URL,
            payload=urllib.urlencode({'q': recent_sql}),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=config.DEADLINE_FETCH
        )

        # Retrieve results. Store the total count if there is one.
        recent_changes = []
        total_count = 0
        if response.status_code != 200:
            message += "<br><strong>Error</strong>: query ('" + recent_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content
            results = {"rows": []}
        else:
            results = json.loads(response.content)
            recent_changes = results['rows']
            if len(recent_changes) > 0:
                total_count = recent_changes[0]['total_count']

        # Render recent changes.
        self.render_template('recent.html', {
            'message': message,
            'login_url': users.create_login_url(BASE_URL),
            'logout_url': users.create_logout_url(BASE_URL),
            'user_url': user_url,
            'user_name': user_name,
            'language_names': languages.language_names,
            'language_names_list': languages.language_names_list,
            'offset': offset,
            'display_count': display_count,
            'total_count': total_count,
            'recent_changes': recent_changes,
            'vneditor_version': version.NOMDB_VERSION
        })

#
# LIST VIEW HANDLER: /list
#
class ListViewHandler(BaseHandler):
    """Display a section of the Big List as a table. This is a pretty sophisticated component, with
    support for several different kinds of filters:
        - filter by dataset
        - filter by blank/missing names

    There is an overcomplicated dict-based system for building the SQL query for these. Definitely
    worth cleaning up if possible.
    """

    @staticmethod
    def filter_by_datasets(request, results):
        """ If given any $dataset in the request, select scientific names from those datasets only. """
        datasets = request.get_all('dataset')
        if 'all' in datasets:
            datasets = []

        if len(datasets) > 0:
            results['select'].append("array_agg(DISTINCT LOWER(dataset))")

        sql_having = []
        for dataset in datasets:
            sql_having.append(nomdb.common.encode_b64_for_psql(dataset.lower()) + " = ANY(array_agg(LOWER(dataset)))")
            results['search_criteria'].append("filter by dataset '" + dataset + "'")

        if len(sql_having) > 0:
            results['having'].append("(" + " OR ".join(sql_having) + ")")

    @staticmethod
    def filter_by_blank_langs(request, results):
        """ Display only scientific names missing a vernacular name in a particular language. """
        blank_langs = request.get_all('blank_lang')
        if 'none' in blank_langs:
            blank_langs = []

        if len(blank_langs) > 0:
            results['select'].append("array_agg(DISTINCT LOWER(lang))")

        sql_having = []
        for lang in blank_langs:
            # sql_having.append("NOT " + vnapi.encode_b64_for_psql(lang.lower()) + " = ANY(array_agg(LOWER(lang)))")
            sql_having.append(
                "NOT array_agg(DISTINCT LOWER(lang)) @> ARRAY[" + nomdb.common.encode_b64_for_psql(lang.lower()) + "]")
            results['search_criteria'].append("filter by language '" + lang + "' being blank")

        if len(sql_having) > 0:
            results['having'].append("(" + " AND ".join(sql_having) + ")")

    def get(self):
        """ Display a filtered list of species names and their vernacular names. """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

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
        def use_last_or_default(argument, default):
            all_vals = self.request.get_all(argument)
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

        ListViewHandler.filter_by_datasets(self.request, results)
        ListViewHandler.filter_by_blank_langs(self.request, results)

        # There's an implicit first filter if there is no filter.
        if len(results['search_criteria']) == 0:
            results['search_criteria'] = ["List all"]
        else:
            results['search_criteria'][0].capitalize()

        # Every query should include scientificname and the total count.
        results['select'].insert(0, "scientificname")
        if FLAG_LIST_DISPLAY_COUNT:
            results['select'].insert(1, "COUNT(*) OVER() AS total_count")

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
        response = urlfetch.fetch(
            access.CDB_URL,
            payload=urllib.urlencode({'q': list_sql}),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=config.DEADLINE_FETCH
        )

        # Process error message or results.
        if response.status_code != 200:
            message += "\n<p><strong>Error</strong>: query ('" + list_sql + "'), server returned error " + str(
                response.status_code) + ": " + response.content + "</p>"
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

        vnames = names.get_vnames(name_list)

        self.render_template('list.html', {
            'vneditor_version': version.NOMDB_VERSION,
            'user_url': user_url,
            'user_name': user_name,
            'language_names_list': languages.language_names_list,
            'language_names': languages.language_names,
            'datasets_data': masterlist.get_dataset_counts(),
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

#
# TESTS PAGE (/tests)
#
class TestsPage(BaseHandler):
    """ Runs a set of consistency checks on the database, looking for
    common errors (read: any oddness we find).
    """

    class TestResult:
        """ Stores the result of a single test. It can succeed or not. """

        def __init__(self, succeeded, message):
            """ Create a new test result.

            :param succeeded: The result of the test: True or False
            :param message: A string description of the test's result.
            :return: A new TestResult object.
            """
            self.message = message
            self.succeeded = True if succeeded else False

        @property
        def succeeded(self):
            """
            :return: True if this test succeeded, else False.
            """
            return self.succeeded

        @property
        def message(self):
            """
            :return: A message relating to this test.
            """
            return self.message

    class TestSet:
        """ A set of tests. """

        def __init__(self, name, description):
            """
            :param name: The name of this set of tests.
            :param description: A description of this set of tests. May contain newlines; URLs will be URLized on display.
            :return: A TestSet object.
            """
            self.name = name
            self.description = description
            self.results = []

        @property
        def name(self):
            """
            :return: The name of this TestSet.
            """
            return self.name

        @property
        def description(self):
            """
            :return: The description of this TestSet.
            """
            return self.description

        @property
        def succeeded(self):
            """
            :return: Returns True if every TestResult in this TestSet is true, False otherwise.
            """
            return len(filter(lambda x: not x.succeeded, self.results)) == 0

        @property
        def results(self):
            """
            :return: Return the list of TestResults that have been executed.
            """
            if len(self.results) == 0:
                return [TestsPage.TestResult(False, "No tests run.")]
            return self.results

        # The following methods add a TestResult to this TestSet.

        def success(self, message):
            """ Add a successful test result.

            :param message: The success message associated with this test result.
            """
            self.results.append(TestsPage.TestResult(True, message))

        def failure(self, message):
            """ Add a failed test result.

            :param message: The failure message associated with this test result.
            """
            self.results.append(TestsPage.TestResult(False, message))

        def ok(self, bool_result, message):
            """ Add a test result -- if bool_result is True, we add a success message; otherwise,
            we add a failure message.

            :param bool_result: Success (True) or failure (False).
            :param message: The message that goes with this.
            """
            # logging.info("ok(" + str(bool_result) + ", '" + str(message) + "'")
            self.results.append(TestsPage.TestResult(bool_result, message))

        def test_sql(self, sql_query, fn_condition, fn_message):
            """
            Run an SQL request against CartoDB, and test the results in some way.

            :param sql_query: An SQL query to pass to CartoDB
            :param fn_condition: A callback function taking the rows returned from CartoDB; returns whether the test succeeded or failed.
            :param fn_message: A callback function taking the rows returned from CartoDB; returns the message for this test result.
            """
            url = access.CDB_URL + "?" + urllib.urlencode({'q': sql_query})
            response = urlfetch.fetch(url)

            # logging.info("Querying: " + url)

            if response.status_code != 200:
                self.failure("Error: server returned error " + str(response.status_code) + ": " + response.content)
                logging.error("Error: server returned error " + str(response.status_code) + ": " + response.content)
                return

            results = json.loads(response.content)
            self.ok(fn_condition(results['rows']), fn_message(results['rows']))

    def test_blank_fields(self):
        """ Some fields in the database should never be blank. We highlight such records here."""

        test = TestsPage.TestSet("blank_fields", inspect.getdoc(self.test_blank_fields))

        # Tests for access.ALL_NAMES_TABLE: scname
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE scname='' OR scname IS NULL" % access.ALL_NAMES_TABLE,
            lambda results: results[0]['count'] == 0,
            lambda results: "All names table: 'scname' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.ALL_NAMES_TABLE: cmname
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE cmname='' OR cmname IS NULL" % access.ALL_NAMES_TABLE,
            lambda results: results[0]['count'] == 0,
            lambda results: "All names table: 'cmname' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.ALL_NAMES_TABLE: lang
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE lang='' OR lang IS NULL" % access.ALL_NAMES_TABLE,
            lambda results: results[0]['count'] == 0,
            lambda results: "All names table: 'lang' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.ALL_NAMES_TABLE: source
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE source='' OR source IS NULL" % access.ALL_NAMES_TABLE,
            lambda results: results[0]['count'] == 0,
            lambda results: "All names table: 'source' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.ALL_NAMES_TABLE: source_priority
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE source_priority='' OR source_priority IS NULL" % access.ALL_NAMES_TABLE,
            lambda results: results[0]['count'] == 0,
            lambda results: "All names table: 'source_priority' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.MASTER_LIST: scientificname
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE scientificname='' OR scientificname IS NULL" % access.MASTER_LIST,
            lambda results: results[0]['count'] == 0,
            lambda results: "Master list: 'scientificname' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.MASTER_LIST: dataset
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE dataset='' OR dataset IS NULL" % access.MASTER_LIST,
            lambda results: results[0]['count'] == 0,
            lambda results: "Master list: 'dataset' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.MASTER_LIST: family
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE family='' OR family IS NULL" % access.MASTER_LIST,
            lambda results: results[0]['count'] == 0,
            lambda results: "Master list: 'family' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        # Tests for access.MASTER_LIST: iucn_red_list_status
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE iucn_red_list_status='' OR iucn_red_list_status IS NULL" % access.MASTER_LIST,
            lambda results: results[0]['count'] == 0,
            lambda results: "Master list: 'family' column contains %d rows that are blank or null." % (results[0]['count'])
        )

        return test

    def test_field_range(self):
        """ Test the range of some values in the database."""

        test = TestsPage.TestSet("test_field_range", inspect.getdoc(self.test_field_range))

        # Tests for access.MASTER_LIST: source_priority within config.PRIORITY_MIN to config.PRIORITY_MAX.
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE source_priority::INT < %d OR source_priority::INT > %d" % (
                access.ALL_NAMES_TABLE,
                config.PRIORITY_MIN,
                config.PRIORITY_MAX
            ),
            lambda results: results[0]['count'] == 0,
            lambda results:
                "All names table: 'source_priority' column contains %d rows that are less than %d (PRIORITY_MIN) or greater than %d (PRIORITY_MAX)." %
                    (results[0]['count'], config.PRIORITY_MIN, config.PRIORITY_MAX)
        )

        # Tests for access.MASTER_LIST: iucn_red_list_status IN ('CD', 'EX', 'EW', 'CR', 'EN', 'VU', 'NT', 'LC', 'DD', 'NE')
        test.test_sql(
            "SELECT COUNT(*) AS count FROM %s WHERE iucn_red_list_status != '' AND iucn_red_list_status NOT IN ('CD', 'EX', 'EW', 'CR', 'EN', 'VU', 'NT', 'LC', 'DD', 'NE')" %
                access.MASTER_LIST,
            lambda results: results[0]['count'] == 0,
            lambda results: "Master list: 'iucn_red_list_status' column contains %d values that are not valid IUCN Red List statuses." %
                (results[0]['count'])
        )

        return test

    def get(self):
        """ Display a filtered list of species names and their vernacular names. """
        self.response.headers['Content-type'] = 'text/html'

        # Check user.
        user = self.check_user()
        user_name = user.email() if user else "no user logged in"
        user_url = users.create_login_url(BASE_URL)

        if user is None:
            return

        # Message?
        message = self.request.get('msg')

        # Run tests and see what the response is.
        tests = list()
        tests.append(self.test_blank_fields())
        tests.append(self.test_field_range())

        # Display test results.
        self.render_template('tests.html', {
            'vneditor_version': version.NOMDB_VERSION,
            'user_url': user_url,
            'user_name': user_name,
            'message': message,
            'tests': tests
        })

#
# ROUTING FOR APPLICATION
#
application = webapp2.WSGIApplication([
    # If someone tries accessing the module directly, display the root page,
    # but all links from here should go to another
    ('/', MainPage),

    # Pages on our website.
    (BASE_URL, MainPage),
    (BASE_URL + '/search', SearchPage),
    (BASE_URL + '/add/name', AddNameHandler),
    (BASE_URL + '/page/private', StaticPages),
    (BASE_URL + '/list', ListViewHandler),
    (BASE_URL + '/delete/cartodb_id', DeleteNameByCDBIDHandler),
    (BASE_URL + '/recent', RecentChangesHandler),
    (BASE_URL + '/taxonomy', HigherTaxonomyHandler),
    (BASE_URL + '/family', FamilyHandler),
    (BASE_URL + '/hemihomonyms', HemihomonymHandler),
    (BASE_URL + '/sources', SourcesHandler),
    (BASE_URL + '/coverage', CoverageViewHandler),
    (BASE_URL + '/import', BulkImportHandler),
    (BASE_URL + '/masterlist', MasterListHandler),
    (BASE_URL + '/tests', TestsPage)
], debug=not PROD)
