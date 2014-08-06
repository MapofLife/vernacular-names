# vim: set fileencoding=utf-8 :

from google.appengine.api import users, urlfetch

import base64
import os
import webapp2
import jinja2
import json
import urllib
import re

# Configuration
import access

# Our libraries
import vnapi

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

        # Do the search.
        search_results = dict()
        search_results_scnames = []
        if current_search != '':
            search_results = vnapi.searchForName(current_search)
            search_results_scnames = sorted(search_results.keys())

        # Do the lookup
        lookup_search = self.request.get('lookup')
        lookup_results = {}
        if lookup_search != '':
            lookup_results = vnapi.getVernacularNames(lookup_search)

        lookup_results_lang_names = dict()
        language_names = {
            'en': u'English',
            'es': u'Spanish (Español)',
            'pt': u'Portuguese (Português)',
            'de': u'German (Deutsch)',
            'fr': u'French (le Français)',
            'zh': u'Chinese (中文)'
        }

        for lang in lookup_results:
            if lang in language_names:
                lookup_results_lang_names[lang] = language_names[lang]
            else:
                lookup_results_lang_names[lang] = lang

        self.render_template('main.html', {
            'message': self.request.get('msg'),
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
            'lookup_results_language_names': lookup_results_lang_names
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
            source_priority = 0

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
        )))

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/index.html', MainPage),
    ('/add/name', AddNameHandler),
    ('/page/private', StaticPages)
], debug=not PROD)
