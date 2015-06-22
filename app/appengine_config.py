import sys
import os.path

# Add our included libraries to the path so we can use them.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'config'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib/python-titlecase'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib/urlfetch'))

# Activate Appstats (from https://cloud.google.com/appengine/docs/python/tools/appstats)
def webapp_add_wsgi_middleware(app):
  from google.appengine.ext.appstats import recording
  app = recording.appstats_wsgi_middleware(app)
  return app