# vim: set fileencoding=utf-8 : 

"""masterlist.py: Manages the Master List, which tells us which dataset a name belongs to."""

import json
import urllib
import logging

import nomdb.common
import access

def get_datasets():
    """Return a list of every dataset in the master list."""
    sql = "SELECT DISTINCT dataset FROM %s ORDER BY dataset DESC"
    response = nomdb.common.url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql % (access.MASTER_LIST))
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    results = json.loads(response.content)
    return results['rows']

def get_master_list():
    """Return a list of every scientific name in every dataset."""
    # TODO: we could make this faster by querying it once from the master list.
    datasets = get_datasets()
    all_names = set()
    for dataset in datasets:
        all_names.update(get_dataset_names(dataset['dataset']))
    return list(all_names)

def get_dataset_counts():
    """Return a list of every dataset in the master list."""
    sql = "SELECT dataset, COUNT(*) AS count FROM %s GROUP BY dataset ORDER BY count DESC"
    response = nomdb.common.url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql % (access.MASTER_LIST))
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    results = json.loads(response.content)
    return results['rows']

def get_dataset_names(dataset):
    """Return a list of every scientific name in this dataset."""
    sql = "SELECT scientificname FROM %s WHERE dataset=%s"
    sql_query = sql % (access.MASTER_LIST, nomdb.common.encode_b64_for_psql(dataset))
    response = nomdb.common.url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql_query)
    ))

    if response.status_code != 200:
        raise RuntimeError("Could not read server response (to '" + sql_query + "'): " + response.content)

    results = json.loads(response.content)
    scnames = map(lambda x: x['scientificname'], results['rows'])

    return scnames

def get_dataset_coverage(datasets, langs):
    """Get the dataset coverage for a particular dataset."""
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
        ", ".join(map(lambda dataset: nomdb.common.encode_b64_for_psql(dataset), datasets))
    )

    response = nomdb.common.url_post(access.CDB_URL, dict(q = sql_query))
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
        ", ".join(map(lambda dataset: nomdb.common.encode_b64_for_psql(dataset), datasets))
    )

    response = nomdb.common.url_post(access.CDB_URL, dict(q = sql_query))
    if response.status_code != 200:
        raise RuntimeError("Could not read server response: " + response.content)

    genus_lookups_by_row = json.loads(response.content)['rows']

    logging.info(" - genus lookups complete")

    # Group by dataset, so we can go through the data dataset by dataset.
    species_by_dataset = nomdb.common.group_by(species_lookups, 'dataset')
    genus_by_dataset = nomdb.common.group_by(genus_lookups_by_row, 'dataset')
    
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
            genus_by_scname = nomdb.common.group_by(genus_by_dataset[dataset], 'scname')

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

def dataset_contains_name(dataset, scname):
    """Check if a dataset contains name. It caches the entire list
    of names in that dataset to make its job easier."""
    if dataset not in dataset_contains_name.cache:
        dataset_contains_name.cache[dataset] = dict()
        for scname in get_dataset_names(dataset):
            dataset_contains_name.cache[dataset][scname.lower()] = 1

    result = (scname.lower() in dataset_contains_name.cache[dataset])

    return result

# Initialize cache
dataset_contains_name.cache = dict()

def search_for_name(name):
    """ Search for a scientific name or vernacular name using a LIKE pattern. Names in the vernacular name table
    will be returned, along with a field that indicates whether they are listed in the master list or not.

    This pattern will be used to search both scnames and vnames.

    :return: A dictionary with keys = scientific names and values = list of common names that match the query.
    :rtype : dict
    """

    # TODO: sanitize input

    # Escape any characters that might be used in a LIKE pattern
    # From http://www.postgresql.org/docs/9.1/static/functions-matching.html

    search_pattern = name.replace("_", "__").replace("%", "%%")

    sql = "SELECT DISTINCT scname, (scientificname IS NOT NULL) AS flag_in_master_list, cmname FROM %s RIGHT JOIN %s ON (LOWER(scname)=LOWER(scientificname)) WHERE LOWER(scname) LIKE %s OR LOWER(cmname) LIKE %s ORDER BY scname ASC"
    response = nomdb.common.url_get(access.CDB_URL + "?" + urllib.urlencode(
        dict(q = sql % (
            access.MASTER_LIST, access.ALL_NAMES_TABLE, 
            nomdb.common.encode_b64_for_psql("%" + name.lower() + "%"),
            nomdb.common.encode_b64_for_psql("%" + name.lower() + "%")
        ))
    ))

    if response.status_code != 200:
        raise "Could not read server response: " + response.content

    rows = json.loads(response.content)['rows']

    match_table = dict()
    for row in rows:
        scname = row['scname']

        if not scname in match_table:
            # HACK! We create a 'common name' of 'flag_in_master_list' to store whether or not
            # this name is in the Master List. It should be ignored when displaying the results.
            match_table[scname] = {
                '_flag_in_master_list': row['flag_in_master_list']
            }

        if row['cmname'].lower().find(name.lower()) >= 0:
            match_table[scname][row['cmname']] = 1

    # Hacky!

    return match_table

def get_higher_taxonomy(scnames):
    """ Retrieve the higher taxonomy for these scientific names.

    I'll write something that returns sources too if that becomes necessary.

    :param scnames: Scientific names to look up.
    :return: a dict() with scientific names as the keys. Values are a dict containing rank in lowercase and the
    higher taxonomy as its value.
    """

    # For now, we only have 'family' and we retrieve that from access.MASTER_LIST.
    sql = """SELECT
            scientificname,
            array_agg(family ORDER BY cartodb_id ASC) AS agg_family,
            array_agg(family_source ORDER BY cartodb_id ASC) AS agg_family_source
        FROM %s
        WHERE LOWER(scientificname) IN (%s)
        GROUP BY scientificname"""
    query = sql % (
        access.MASTER_LIST,
        ", ".join(map(lambda x: nomdb.common.encode_b64_for_psql(x.lower()), scnames))
    )
    # print("DEBUG: " + query + ".")
    response = nomdb.common.url_post(access.CDB_URL, {'q': query})

    if response.status_code != 200:
        raise "Could not read server response: " + response.content

    rows = json.loads(response.content)['rows']

    results = dict()
    for row in rows:
        scname = row['scientificname']
        agg_family = row['agg_family']
        agg_family_source = row['agg_family_source']

        if len(agg_family) == 0:
            raise RuntimeError("Scientific name '%s' does not have a family name in the master list!" % scname)

        results[scname.lower()] = {
            'family': agg_family[0],
            'family_source': dict(zip(agg_family, agg_family_source))[agg_family[0]]
        }

    return results
