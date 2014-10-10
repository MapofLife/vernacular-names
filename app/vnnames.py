# vim: set fileencoding=utf-8 : 

# vnnames.py
# Functions for working with vernacular names 

from google.appengine.api import urlfetch

import logging

import vnapi
import urllib
import json
import re
import os

import languages
import access

# Constants
SEARCH_CHUNK_SIZE = 2000        # Number of names to query at once.
FLAG_LOOKUP_GENERA = False      # Look up genera when scientific names could not be matched.

# Check whether we're in production (PROD = True) or not.
PROD = True
if 'SERVER_SOFTWARE' in os.environ:
    PROD = not os.environ['SERVER_SOFTWARE'].startswith('Development')

# If we're not in PROD, change some stuff.
if not PROD:
    logging.info("Developer environment detected, activating genus lookups.")
    FLAG_LOOKUP_GENERA = True

# Datatypes
class VernacularName:
    def __init__(self, scientificName, lang, vernacularName, sources, tax_class, tax_order, tax_family):
        self.scname = scientificName
        self.lang = lang
        self.cmname = vernacularName
        self.sources = sources
        self.tax_class = tax_class
        self.tax_order = tax_order
        self.tax_family = tax_family

    def __repr__(self):
        return "<'%s'@%s [%d sources]>" % (
            self.cmname,
            self.lang,
            len(self.sources)
        )

    @property
    def scientificname(self):
        return self.scname

    @property
    def lang(self):
        return self.lang

    @property
    def vernacularname(self):
        return self.cmname

    @property
    def sources(self):
        return set(self.sources)

    @property
    def tax_class(self):
        return self.tax_class

    @property
    def tax_order(self):
        return self.tax_order

    @property
    def tax_family(self):
        return self.tax_family

# getVernacularNames: Returns vernacular names for this scientific name in
# every language for which we have data.
#
# Input: 
#   - names: list of names
#   - flag_no_higher: do not look up higher taxonomy
#   - flag_no_memoize: do not save query and return the cached result
#   - flag_all_results: return all vernacular name results
#
# Output: a dict in the format --
#   results[name1][lang] = VernacularName object 1
#   results[name1]['tax_order'] = lc(order)
#   results[name2][lang] = VernacularName object 2
#     ...
#   results[nameN][lang]
#

getVernacularNames_cache = dict()
def getVernacularNames(names, flag_no_higher=False, flag_no_memoize=False, flag_all_results=False):
    namekey = "|".join(sorted(names))
    if not flag_no_memoize and namekey in getVernacularNames_cache:
        return getVernacularNames_cache[namekey]

    results = dict()

    def addToDict(name, higher_taxonomy, vnames_by_lang):
        if name in results:
            raise RuntimeError("Duplicate name in getNames")

        results[name] = vnames_by_lang
        results[name]['tax_order'] = higher_taxonomy['order']
        results[name]['tax_class'] = higher_taxonomy['class']
        results[name]['tax_family'] = higher_taxonomy['family']

    searchVernacularNames(addToDict, names, flag_no_higher, flag_all_results)
    
    if not flag_no_memoize:
        getVernacularNames_cache[namekey] = results
    return results

# searchVernacularNames(fn_callback, query_names, flag_no_higher, flag_all_results)
#
# Input:
#   - fn_callback -> (name, higher_taxonomy, vnames_by_lang)
#       A callback function called once the higher taxonomy is sorted for
#       each name.
#           name: the scientific name (as in 'names')
#           higher_taxonomy: {
#               tax_class: The class in lowercase
#               tax_order: The order in lowercase
#               tax_family: The family in lowercase
#           }
#           vnames_by_lang: {
#               'en': VernacularName object
#               'fr': VernacularName object
#                   ...
#           }
#   - query_names: list of names to search through. Since we send this to 
#       CartoDB in chunks, this should be as long as possible.
#   - flag_no_higher: don't recurse into higher taxonomy.
#   - flag_all_results: return all results, not just the best one
#
def searchVernacularNames(fn_callback, query_names, flag_no_higher=False, flag_all_results=False):
    # Reassert uniqueness and sort names. We need to sort them because
    # sets are not actually iterable.
    query_names_sorted = sorted(set(query_names))

    # Log.
    if len(query_names_sorted) >= 10:
        logging.info("searchVernacularNames called with %d names." % (len(query_names_sorted)))

    # From http://stackoverflow.com/a/312464/27310
    def chunks(items, size):
        for i in xrange(0, len(items), size):
            yield items[i:i+size]

    # Query through all names in chunks.
    row = 0
    for chunk_names in chunks(query_names_sorted, SEARCH_CHUNK_SIZE):
        row += len(chunk_names)

        # Only display progress on the main task.
        if row >= 10:
            logging.info("Downloaded %d rows of %d names." % (row, len(query_names_sorted)))

        # Get higher taxonomy, language, common name.
        # TODO: fallback to trinomial names (where we have Panthera tigris tigris but not Panthera tigris.
        scientificname_list = ", ".join(
            map(lambda name: vnapi.encode_b64_for_psql(name.lower()), chunk_names)
        )

        sql = """
            SELECT 
                scname,
                LOWER(scname) AS scname_lc,
                array_agg(DISTINCT LOWER(tax_order)) AS agg_order, 
                array_agg(DISTINCT LOWER(tax_class)) AS agg_class, 
                array_agg(DISTINCT LOWER(tax_family)) AS agg_family, 
                lang, 
                cmname, 
                array_agg(source) AS sources, 
                COUNT(DISTINCT LOWER(source)) AS count_sources,
                array_agg(url) AS urls, 
                MAX(updated_at) AS max_updated_at, 
                MAX(source_priority) AS max_source_priority 
            FROM %s 
            WHERE 
                LOWER(scname) IN (%s) 
            GROUP BY 
                scname, lang, cmname 
            ORDER BY
                max_source_priority DESC, 
                count_sources DESC,
                max_updated_at DESC
        """
        sql_query = sql % (
            access.ALL_NAMES_TABLE,
            scientificname_list
        )

        urlresponse = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(dict(
                q = sql_query
            )),
            method=urlfetch.POST,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            deadline = vnapi.DEADLINE_FETCH
        )

        if urlresponse.status_code != 200:
            raise IOError("Could not read from CartoDB: " + str(response.status_code) + ": " + str(response.content))
            
        results = json.loads(urlresponse.content)
        rows_by_scname = vnapi.groupBy(results['rows'], 'scname_lc')

        def clean_agg(list):
            return set(filter(lambda x: x is not None and x != '', list))

        # Process each input name in this chunk, not just every resulting name.
        for query_scname in chunk_names:
            scname = query_scname.lower()
            
            results = []

            if scname in rows_by_scname:
                results = rows_by_scname[scname]

            results_by_lang = vnapi.groupBy(results, 'lang')

            best_names = dict()
            taxonomy = {
                'class': set(),
                'order': set(),
                'family': set()
            }
            
            # Figure out class, order and family.
            for lang in results_by_lang:
                lang_results = results_by_lang[lang]

                for result in lang_results:
                    taxonomy['class'].update(clean_agg(result['agg_class']))
                    taxonomy['order'].update(clean_agg(result['agg_order']))
                    taxonomy['family'].update(clean_agg(result['agg_family']))

            # Prevent recursion: remove any higher taxonomy
            # that is part of our current query.
            taxonomy['class'] = set(filter(lambda x: x.lower() != scname, taxonomy['class']))
            taxonomy['order'] = set(filter(lambda x: x.lower() != scname, taxonomy['order']))
            taxonomy['family'] = set(filter(lambda x: x.lower() != scname, taxonomy['family']))

            # For every language we are interested in.
            for lang in languages.language_names_list:
                def simplify_to_list(simplify_names):
                    if flag_no_higher:
                        return set()

                    vnames = set()
                    vnresults = getVernacularNames(simplify_names, flag_no_higher=True)

                    for simplify_name in simplify_names:
                        if not lang in vnresults[simplify_name]:
                            continue

                        vnames.add(vnresults[simplify_name][lang].vernacularname.capitalize())

                    return clean_agg(vnames)

                vn_tax_class = simplify_to_list(taxonomy['class'])
                vn_tax_order = simplify_to_list(taxonomy['order'])
                vn_tax_family = simplify_to_list(taxonomy['family'])
                vn_vernacularname = ""
                vn_sources = set()

                vn_all_entries = []

                if (lang in results_by_lang) and (len(results_by_lang[lang]) > 0):
                    lang_results = results_by_lang[lang]

                    if flag_all_results:
                        for result in lang_results:
                            vn_all_entries.append(VernacularName(
                                scname, lang,
                                result['cmname'],
                                result['sources'],
                                vn_tax_class,
                                vn_tax_order,
                                vn_tax_family
                            ))
                    else:
                        vn_vernacularname = lang_results[0]['cmname']
                        vn_sources = lang_results[0]['sources']
                else:
                    if FLAG_LOOKUP_GENERA:
                        # No match? Try genus?
                        match = re.search('^(\w+)\s+(\w+)$', scname)
                        if match:
                            genus = match.group(1)
                            genus_matches = getVernacularNames([genus])

                            if genus in genus_matches:
                                if lang in genus_matches[genus]:
                                    vn_vernacularname = genus_matches[genus][lang].vernacularname
                                    vn_sources = genus_matches[genus][lang].sources

                                    #if vn_vernacularname != '':
                                    #    logging.info("genus '" + genus + "' looked up in '" + lang + "' and found: '" + vn_vernacularname + "'")

                                if len(taxonomy['class']) == 0:
                                    taxonomy['class'] = genus_matches[genus]['tax_class']
                                    vn_tax_class = simplify_to_list(taxonomy['class'])

                                if len(taxonomy['order']) == 0:
                                    taxonomy['order'] = genus_matches[genus]['tax_order']
                                    vn_tax_order = simplify_to_list(taxonomy['order'])
     
                                if len(taxonomy['family']) == 0:
                                    taxonomy['family'] = genus_matches[genus]['tax_family']
                                    vn_tax_family = simplify_to_list(taxonomy['family'])

                if flag_all_results:
                    best_names[lang] = []
                    for vname in vn_all_entries:
                        best_names[lang].append(VernacularName(
                            vname.scientificname,
                            vname.lang,
                            vname.vernacularname,
                            vname.sources,
                            vn_tax_class,
                            vn_tax_order,
                            vn_tax_family
                        ))
                else:
                    best_names[lang] = VernacularName(
                        scname,
                        lang,
                        vn_vernacularname,
                        vn_sources,
                        vn_tax_class,
                        vn_tax_order,
                        vn_tax_family
                    )

            fn_callback(query_scname, taxonomy, best_names)

