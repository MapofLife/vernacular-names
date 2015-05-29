# vim: set fileencoding=utf-8 :

"""common.py: Common functions and constants used by NomDB."""

import base64
import logging
import re
import urllib

from nomdb.config import DEADLINE_FETCH
from titlecase import titlecase
import urlfetch

__author__ = 'Gaurav Vaidya'

# Helper functions.
# In order to allow code to be used locally, we set up url_get and url_post methods.
# These use google.appengine.api.urlfetch when running on the Google App Engine,
# and the local urlfetch library when running locally.
import importlib

gae_urlfetch = None

try:
    gae_urlfetch = importlib.import_module('google.appengine.api.urlfetch')
    gae_urlfetch.set_default_fetch_deadline(DEADLINE_FETCH)
except ImportError:
    pass

def url_get(url):
    """Retrieve a URL using HTTP GET."""
    if gae_urlfetch:
        logging.info("url_get(" + url + ") with GAE")
        return gae_urlfetch.fetch(url)
    else:
        logging.info("url_get(" + url + ") with urlfetch")
        return urlfetch.fetch(url, deadline =DEADLINE_FETCH)

def url_post(url, data):
    """Retrieve a URL using HTTP POST, submitting 'data' as a dict.
       'data' is URL-encoded before transmission."""
    if gae_urlfetch:
        logging.info("url_post(" + url + ") with GAE")
        return gae_urlfetch.fetch(url,
            payload=urllib.urlencode(data),
            method=gae_urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=DEADLINE_FETCH
        )
    else:
        logging.info("url_post(" + url + ") with urlfetch")
        return urlfetch.post(url, data = data)

def decode_b64_on_psql(text):
    """Prepare a bit of code for PostgreSQL to decode a string on the server side.
    You probably don't need to use this."""
    base64_only = re.compile(r"^[a-zA-Z0-9+/=]*$")
    if not base64_only.match(text):
        raise RuntimeError("Error: '" + text + "' sent to decode_b64_on_psql is not base64!")

    return "convert_from(decode('" + text + "', 'base64'), 'utf-8')"


def encode_b64_for_psql(text):
    """Encode a Unicode string as base64, then set it up to be decoded on the server.
    You probably need to use this."""
    return decode_b64_on_psql(base64.b64encode(text.encode('UTF-8')))

# TODO This is something PostgreSQL should be able to handle, so we should refactor this out and delete it entirely.
def group_by(rows, colname):
    """Given a list of rows, divide them until into groups of rows by the values
    in the column provided in 'colName'. Return this as a dict.
    """
    result_table = dict()

    for row in rows:
        val = row[colname]

        if not val in result_table:
            result_table[val] = []

        result_table[val].append(row)

    return result_table


def format_name(name):
    """Utility function: format a common name.
    This slows us by about 50% (44 mins for a full genus generation)
    """
    return titlecase(name)