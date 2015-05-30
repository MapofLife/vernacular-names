# vim: set fileencoding=utf-8 : 

"""masterlist.py: Manages the Master List, which tells us which dataset a name belongs to."""

import json
import urllib
import logging

from nomdb.common import url_get, url_post, encode_b64_for_psql, group_by
import access

# Return a list of every dataset in the master list.
def getDatasets():
    sql = "SELECT DISTINCT dataset FROM %s ORDER BY dataset DESC"
    response = url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql % (access.MASTER_LIST))
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    results = json.loads(response.content)
    return results['rows']

# Return a list of every dataset in the master list.
def getDatasetCounts():
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
    species_by_dataset = group_by(species_lookups, 'dataset')
    genus_by_dataset = group_by(genus_lookups_by_row, 'dataset')
    
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
            genus_by_scname = group_by(genus_by_dataset[dataset], 'scname')

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
                    coverage[lang] = {'count': 0, 'as_species': 0, 'as_genus': 0, 'as_unmatched': 0}

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
        for scname in getDatasetNames(dataset):
            datasetContainsName.cache[dataset][scname.lower()] = 1

    result = (scname.lower() in datasetContainsName.cache[dataset])

    return result

# Initialize cache
datasetContainsName.cache = dict()

# Note: only searches names in the master list.
def searchForName(name):
    """

    :rtype : dict
    """

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
