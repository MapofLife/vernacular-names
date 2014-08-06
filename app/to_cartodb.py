from google.appengine.api import users, urlfetch

import os
import webapp2
import jinja2
import json
import urllib

# Configuration
CDB_URL = "http://mol.cartodb.com/api/v2/sql?%s"
DB_TABLE_NAME = "vernacular_names_no_newlines_top1m"

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
        user_name = user.nickname() if user else "no user logged in"
        user_url = users.create_login_url('/')

        current_search = self.request.get('search')
        if self.request.get('clear') != '':
            current_search = ''

        # Do the search.
        search_results = []
        if current_search != '':
            search_response = self.getNames(current_search)
            if search_response.status_code == 200:
                search_results = json.loads(search_response.content)['rows']

        # Do the lookup
        lookup_search = self.request.get('lookup')
        lookup_results = {}
        if lookup_search != '':
            lookup_results = self.getNamesLookup(lookup_search)

        self.render_template('main.html', {
            'login_url': users.create_login_url('/'),
            'logout_url': users.create_logout_url('/'),
            'user_url': user_url,
            'user_name': user_name,
            'current_search': current_search,
            'search_results': search_results,
            'lookup_search': lookup_search,
            'lookup_results': lookup_results,
            'lookup_results_languages': sorted(lookup_results.keys())
        })

    def getNamesLookup(self, name):
        # TODO: sanitize input
        sql = "SELECT DISTINCT lang, cmname, source_priority FROM %s WHERE scname = '%s' ORDER BY source_priority ASC"
        response = urlfetch.fetch(CDB_URL % urllib.urlencode(dict(q = sql % (
            DB_TABLE_NAME, name
        ))), deadline=60)
        if response.status_code != 200:
            return dict()
        results = json.loads(response.content)

        result_table = dict()
        for row in results['rows']:
            lang = row['lang']

            if not lang in result_table:
                result_table[lang] = []

            result_table[lang].append(row['cmname'] + " [" + row['source_priority'] + "]")

            # TODO: source

        return result_table

    def getNames(self, name):
        # TODO: sanitize input
        
        # Escape any characters that might be used in a LIKE pattern
        # From http://www.postgresql.org/docs/9.1/static/functions-matching.html
        search_pattern = name.replace("_", "__").replace("%", "%%")

        sql = "SELECT DISTINCT scname, cmname FROM %s WHERE scname LIKE '%%%s%%' OR cmname LIKE '%%%s%%' ORDER BY scname ASC"
        return urlfetch.fetch(CDB_URL % urllib.urlencode(dict(q = sql % (
            DB_TABLE_NAME, name, name
        ))))

class GetVernacularNames(BaseHandler):
    def get(self):
        user = self.check_user()

        self.response.headers['Content-Type'] = 'text/plain'
        self.response.write('[{"scientificName": "Panthera tigris", "vernacularName": "tiger", "lang": "en", "source": "me"}]')

application = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/index.html', MainPage),
    ('/page/private', StaticPages),
    ('/get', GetVernacularNames),
], debug=not PROD)
