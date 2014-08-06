# vim: set fileencoding=utf-8 : 

# vnapi.py
# An API for communicating with the Vernacular Name system
# on CartoDB.

from google.appengine.api import urlfetch

import base64
import json
import urllib
import re

# Authentication
import access

# Configuration
DEADLINE_FETCH = 60 # seconds to wait during URL fetch

def url_get(url):
    return urlfetch.fetch(url, deadline = DEADLINE_FETCH)

def encode_b64_for_psql(text):
    return decode_b64_on_psql(base64.b64encode(text))

def decode_b64_on_psql(text):                                         
    base64_only = re.compile(r"^[a-zA-Z0-9=]*$")                            
    if not base64_only.match(text):                                         
        raise RuntimeError("Error: '" + text + "' sent to decode_b64_on_psql is not base64!")

    return "convert_from(decode('" + text + "', 'base64'), 'utf-8')"        

def sortNames(rows):
    result_table = dict()                                                   
        
    for row in rows:                                             
        lang = row['lang']                                                  

        if not lang in result_table:                                        
            result_table[lang] = []

        result_table[lang].append(dict(
            cmname = row['cmname'],
            source = row['source'],
            source_priority = int(row['source_priority'])
        ))

    return result_table 

def getVernacularNames(name):
    # TODO: sanitize input                                                  
    sql = "SELECT DISTINCT lang, cmname, source, source_priority FROM %s WHERE LOWER(scname) = %s ORDER BY source_priority DESC"
    response = url_get(access.CDB_URL % urllib.urlencode(
        dict(q = sql % (access.ALL_NAMES_TABLE, encode_b64_for_psql(name.lower())))
    ))

    if response.status_code != 200: 
        raise "Could not read server response: " + response.content

    results = json.loads(response.content)                                  
    return sortNames(results['rows'])

def searchForName(name):
    # TODO: sanitize input                                                  

    # Escape any characters that might be used in a LIKE pattern            
    # From http://www.postgresql.org/docs/9.1/static/functions-matching.html
    search_pattern = name.replace("_", "__").replace("%", "%%")             

    sql = "SELECT DISTINCT scname, cmname FROM %s WHERE LOWER(scname) LIKE %s OR LOWER(cmname) LIKE %s ORDER BY scname ASC"
    response = url_get(access.CDB_URL % urllib.urlencode(
        dict(q = sql % (
            access.ALL_NAMES_TABLE, 
            encode_b64_for_psql("%" + name.lower() + "%"), 
            encode_b64_for_psql("%" + name.lower() + "%")
        ))
    ))

    if response.status_code != 200:
        raise "Could not read server response: " + response.content

    matches = json.loads(response.content)['rows']  

    match_table = dict()
    for match in matches:
        scname = match['scname']

        if not scname in match_table:
            match_table[scname] = []

        if match['cmname'].find(name) >= 0:
            match_table[scname].append(match['cmname'])

    return match_table
