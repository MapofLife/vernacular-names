import sys
import os.path

# Add our included libraries to the path so we can use them.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'config'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib/python-titlecase'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib/urlfetch'))
