# vim: set fileencoding=utf-8 : 

# vnapi.py
# An API for communicating with the Vernacular Name system on CartoDB.

import urlfetch

import base64
import json
import urllib
import re
import logging

import access
import vnnames

# Configuration.
DEADLINE_FETCH = 60 # seconds to wait during URL fetch (max: 60)

# Min, max and default values for source_priority
PRIORITY_MIN = 0
PRIORITY_MAX = 100
PRIORITY_DEFAULT = 0
PRIORITY_DEFAULT_APP = 80

# Helper functions.
# Tricky: try to load google.appengine.api.urlfetch;
# if so, use that instead of urlfetch.
import importlib
gae_urlfetch = None
try:
    gae_urlfetch = importlib.import_module('google.appengine.api.urlfetch')
    gae_urlfetch.set_default_fetch_deadline(DEADLINE_FETCH)
except ImportError:
    pass

def url_get(url):
    if gae_urlfetch:
        logging.info("url_get(" + url + ") with GAE")
        return gae_urlfetch.fetch(url)
    else:
        logging.info("url_get(" + url + ") with urlfetch")
        return urlfetch.fetch(url, deadline = DEADLINE_FETCH)

def url_post(url, data):
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

# Encode a Unicode string as base64, then set it up to be decoded on the server.
def encode_b64_for_psql(text):
    return decode_b64_on_psql(base64.b64encode(text.encode('UTF-8')))

# Prepare a bit of code for PostgreSQL to decode a string on the server side.
def decode_b64_on_psql(text):
    base64_only = re.compile(r"^[a-zA-Z0-9+/=]*$")
    if not base64_only.match(text):
        raise RuntimeError("Error: '" + text + "' sent to decode_b64_on_psql is not base64!")

    return "convert_from(decode('" + text + "', 'base64'), 'utf-8')"

# Given a list of rows, divide them until into groups of rows by the values
# in the column provided in 'colName'. Return this as a dict.
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
    response = url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql % (access.MASTER_LIST))
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    results = json.loads(response.content)
    return results['rows']

# Return a list of every scientific name in every dataset.
def getMasterList():
    datasets = getDatasets()
    all_names = set()
    for dataset in datasets:
        all_names.update(getDatasetNames(dataset['dataset']))
    return all_names

# Return a list of every scientific name in this dataset.
def getDatasetNames(dataset):
    sql = "SELECT scientificname FROM %s WHERE dataset=%s"
    sql_query = sql % (access.MASTER_LIST, encode_b64_for_psql(dataset))
    response = url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql_query)
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response (to '" + sql_query + "'): " + response.content)

    results = json.loads(response.content)
    scnames = map(lambda x: x['scientificname'], results['rows'])

    return scnames

# Get the dataset coverage for a particular dataset.
def getDatasetCoverage(datasets, langs):
    logging.info("getDatasetCoverage('" + ", ".join(datasets) + "')")

    # Retrieve names with languages without genus lookups.
    sql = """
        SELECT 
            ml.dataset AS dataset,
            LOWER(ml.scientificname) AS scname, 
            ARRAY_AGG(DISTINCT LOWER(vn.lang)) AS langs
        FROM
            %s ml
            LEFT OUTER JOIN %s vn
            ON LOWER(ml.scientificname) = LOWER(vn.scname)
        WHERE 
            dataset IN (%s)
        GROUP BY ml.dataset, ml.scientificname
    """
    sql_query = sql % (
        access.MASTER_LIST, access.ALL_NAMES_TABLE, 
        ", ".join(map(lambda dataset: encode_b64_for_psql(dataset), datasets))
    )

    response = url_post(access.CDB_URL, dict(q = sql_query))
    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    species_lookups = json.loads(response.content)['rows']

    logging.info(" - species lookups complete")
    
    # Retrieve names with languages with genus lookups.
    sql = """
        SELECT
            ml.dataset AS dataset,
            LOWER(ml.scientificname) AS scname, 
            ARRAY_AGG(DISTINCT LOWER(vn.lang)) AS langs
        FROM
            %s ml
            LEFT OUTER JOIN %s vn
            ON LOWER(SPLIT_PART(ml.scientificname, ' ', 1)) = LOWER(vn.scname)
        WHERE 
            dataset IN (%s) 
        GROUP BY ml.dataset, ml.scientificname
    """
    sql_query = sql % (
        access.MASTER_LIST, access.ALL_NAMES_TABLE, 
        ", ".join(map(lambda dataset: encode_b64_for_psql(dataset), datasets))
    )

    response = url_post(access.CDB_URL, dict(q = sql_query))
    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    genus_lookups_by_row = json.loads(response.content)['rows']

    logging.info(" - genus lookups complete")

    # Group by dataset, so we can go through the data dataset by dataset.
    species_by_dataset = groupBy(species_lookups, 'dataset')
    genus_by_dataset = groupBy(genus_lookups_by_row, 'dataset')
    
    logging.info(" - grouping by dataset complete")

    # Process each dataset separately.
    results = dict(
        coverage = dict(),
        num_species = dict()
    )

    for dataset in species_by_dataset:
        species_rows = species_by_dataset[dataset]
        genus_by_scname = dict()
        if dataset in genus_by_dataset:
            genus_by_scname = groupBy(genus_by_dataset[dataset], 'scname')

        # For each scname, figure out if it has a genus name and species name in each language.
        num_species = 0
        coverage = dict()
        for row in species_rows:
            scname = row['scname']
            langs_species = row['langs']
            langs_genus = []
            if scname in genus_by_scname:
                langs_genus = genus_by_scname[scname][0]['langs']

            if len(langs_species) == 1 and langs_species[0] is None:
                langs_species = []
            
            if len(langs_genus) == 1 and langs_genus[0] is None:
                langs_genus = []

            # print("scname = " + scname + ", species = " + ", ".join(langs_species) + ", genus = " + ", ".join(langs_genus))

            num_species += 1

            for lang in langs:
                if lang not in coverage:
                    coverage[lang] = dict(
                        count = 0,
                        as_species = 0,
                        as_genus = 0,
                        as_unmatched = 0
                    )

                coverage[lang]['count'] += 1

                if lang in langs_species:
                    coverage[lang]['as_species'] += 1
                elif lang in langs_genus:
                    coverage[lang]['as_genus'] += 1
                else:
                    coverage[lang]['as_unmatched'] += 1

            results['coverage'][dataset] = coverage
            results['num_species'][dataset] = num_species

    logging.info(" - coverage counting complete")

    # Generate percentages.
    for dataset in results['coverage']:
        coverage = results['coverage'][dataset]
        num_species = results['num_species'][dataset]
        for lang in coverage:
            coverage[lang]['as_species_pc'] = int(coverage[lang]['as_species'])/float(num_species)*100
            coverage[lang]['as_genus_pc'] = int(coverage[lang]['as_genus'])/float(num_species)*100
            coverage[lang]['unmatched_pc'] = int(coverage[lang]['as_unmatched'])/float(num_species)*100

    logging.info(" - coverage summary complete")

    return results

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
    response = url_get(access.CDB_URL + "?" + urllib.urlencode(
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

        if match['cmname'].find(name.lower()) >= 0:
            match_table[scname].append(match['cmname'])

    return match_table
