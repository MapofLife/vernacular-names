# vim: set fileencoding=utf-8 : 

# vnapi.py
# An API for communicating with the Vernacular Name system on CartoDB.

from google.appengine.api import urlfetch
from operator import itemgetter

import base64
import json
import urllib
import re

# Authentication.
import access
import vnnames

# Configuration.
DEADLINE_FETCH = 60 # seconds to wait during URL fetch (max: 60)

# Helper functions.
def url_get(url):
    return urlfetch.fetch(url, deadline = DEADLINE_FETCH)

# Encode a string as base64
def encode_b64_for_psql(text):
    return decode_b64_on_psql(base64.b64encode(text.encode('UTF-8')))

# Prepare a bit of code for PostgreSQL to decode a string on the server side.
def decode_b64_on_psql(text):                                         
    base64_only = re.compile(r"^[a-zA-Z0-9=]*$")
    if not base64_only.match(text):                                         
        raise RuntimeError("Error: '" + text + "' sent to decode_b64_on_psql is not base64!")

    return "convert_from(decode('" + text + "', 'base64'), 'utf-8')"

# Given a list of rows, divide them until into groups of rows by the values
# in the column provided in 'colName'.
def groupBy(rows, colName):
    result_table = dict()

    for row in rows:
        val = row[colName]

        if not val in result_table:
            result_table[val] = []

        result_table[val].append(row)

    return result_table

# Return a list of every dataset in the master list.
def getDatasets():
    sql = "SELECT dataset, COUNT(*) AS count FROM %s GROUP BY dataset ORDER BY count DESC"
    response = url_get(access.CDB_URL % urllib.urlencode(
        dict(q = sql % (access.MASTER_LIST))
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    results = json.loads(response.content)

    return results['rows']

def getDatasetCoverage(dataset, langs):
    return getNamesCoverage(getDatasetNames(dataset), langs)

# Return the coverage we have on this set of names in the specified language.
# This code is based on vnnames.searchVernacularNames()
def getNamesCoverage(query_names, langs):
    query_names_sorted = sorted(set(query_names))

    # From http://stackoverflow.com/a/312464/27310
    def chunks(items, size):
        for i in xrange(0, len(items), size):
            yield items[i:i+size]

    # Counts
    counts = dict()

    # Query through all names in chunks.
    row = 0
    for chunk_names in chunks(query_names_sorted, vnnames.SEARCH_CHUNK_SIZE):
        row += len(chunk_names)

        genera = set()
        for name in chunk_names:
            match = re.search('^(\w+)\s+(\w+)$', name)
            if match:
                genus = match.group(1)
                genera.add(genus)

        chunk_names_with_genera = list(chunk_names)
        chunk_names_with_genera.extend(genera)

        scname_list = ", ".join(map(lambda name: encode_b64_for_psql(name.lower()), chunk_names_with_genera))
        langs_list = ", ".join(map(lambda lang: encode_b64_for_psql(lang), langs))
        sql = """
            SELECT LOWER(scname) AS scname_lc, scname, lang, COUNT(*) AS count FROM %s 
            WHERE LOWER(scname) IN (%s) AND lang IN (%s)
            GROUP BY scname_lc, scname, lang
        """
        sql_query = sql % (access.ALL_NAMES_TABLE, scname_list, langs_list)
        response = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(dict(
                q = sql_query 
            )),
            method=urlfetch.POST,
            headers={'Content-type': 'application/x-www-form-urlencoded'},
            deadline=DEADLINE_FETCH
        )

        if response.status_code != 200:
            raise RuntimeError("Could not read server response: " + response.content)

        results = json.loads(response.content)
        rows_by_lang = groupBy(results['rows'], 'lang')

        for lang in langs:
            if not lang in counts:
                counts[lang] = {
                    'total': 0,
                    'matched_with_species_name': 0,
                    'matched_with_genus_name': 0,
                    'unmatched': 0
                }

            rows_by_scname_lc = groupBy(rows_by_lang[lang], 'scname_lc')
                
            for scname in chunk_names:
                counts[lang]['total'] += 1

                flag_matched = False
                if lang in rows_by_lang:
                    if scname.lower() in rows_by_scname_lc:
                        counts[lang]['matched_with_species_name'] += 1
                        flag_matched = True
                    else:
                        match = re.search('^(\w+)\s+(\w+)$', name)
                        if match:
                            genus = match.group(1)

                            if genus.lower() in rows_by_scname_lc:
                                counts[lang]['matched_with_genus_name'] += 1
                                flag_matched = True

                if not flag_matched:
                    counts[lang]['unmatched'] += 1

    return counts

# Return a list of every scientific name in this dataset.
def getDatasetNames(dataset):
    sql = "SELECT scientificname FROM %s WHERE dataset=%s"
    sql_query = sql % (access.MASTER_LIST, encode_b64_for_psql(dataset))
    response = url_get(access.CDB_URL % urllib.urlencode(
        dict(q = sql_query)
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response (to '" + sql_query + "'): " + response.content)

    results = json.loads(response.content)
    scnames = map(lambda x: x['scientificname'], results['rows'])

    return scnames

# Check if a dataset contains name. It caches the entire list
# of names in that dataset to make its job easier.
def datasetContainsName(dataset, scname):
    if dataset not in datasetContainsName.cache:
        datasetContainsName.cache[dataset] = dict()
        for scname in getNamesInDataset(dataset):
            datasetContainsName.cache[dataset][scname.lower()] = 1

    result = (scname.lower() in datasetContainsName.cache[dataset])

    return result

# Initialize cache
datasetContainsName.cache = dict()

# Note: only searches names in the master list.
def searchForName(name):
    # TODO: sanitize input                                                  

    # Escape any characters that might be used in a LIKE pattern            
    # From http://www.postgresql.org/docs/9.1/static/functions-matching.html
    search_pattern = name.replace("_", "__").replace("%", "%%")             

    sql = "SELECT DISTINCT scname, cmname FROM %s INNER JOIN %s ON (LOWER(scname)=LOWER(scientificname)) WHERE LOWER(scname) LIKE %s OR LOWER(cmname) LIKE %s ORDER BY scname ASC"
    response = url_get(access.CDB_URL % urllib.urlencode(
        dict(q = sql % (
            access.MASTER_LIST, access.ALL_NAMES_TABLE, 
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
