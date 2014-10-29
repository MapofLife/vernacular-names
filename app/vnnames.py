# vim: set fileencoding=utf-8 : 

# vnnames.py
# Functions for working with vernacular names 

from google.appengine.api import urlfetch
from titlecase import titlecase

import logging

import vnapi
import urllib
import json
import re
import os

import access

# Constants
SEARCH_CHUNK_SIZE = 1000        # Number of names to query at once.

# Check whether we're in production (PROD = True) or not.
PROD = True
if 'SERVER_SOFTWARE' in os.environ:
    PROD = not os.environ['SERVER_SOFTWARE'].startswith('Development')

# Utility function: format a common name.
def format_name(name):
    # This slows us by about 50% (44 mins for a full genus generation)
    return titlecase(name)

# Datatypes
class VernacularName:
    def __init__(self, scientificName, flag_uninomial, lang, vernacularName, max_source_priority, sources, tax_class, tax_order, tax_family):
        self.scname = scientificName
        self.flag_uninomial = bool(flag_uninomial)
        self.lang = lang
        self.cmname = vernacularName
        self.max_source_priority = int(max_source_priority)
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
    def flag_uninomial(self):
        return self.flag_uninomial

    @property
    def lang(self):
        return self.lang

    @property
    def vernacularname(self):
        return self.cmname

    @property
    def max_source_priority(self):
        return self.max_source_priority 

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

# A cache for vernacular names. Used by getVernacularNames but NOT by
# searchVernacularNames.
getVernacularNames_cache = dict()

def clearVernacularNamesCache():
    getVernacularNames_cache = dict()

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

def getVernacularNames(names, languages_list, flag_no_higher=False, flag_no_memoize=False, flag_all_results=False, flag_lookup_genera=True, flag_format_cmnames=False):
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

    searchVernacularNames(addToDict, names, languages_list, flag_no_higher, flag_all_results, flag_lookup_genera, flag_format_cmnames)
    
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
def searchVernacularNames(fn_callback, query_names, languages_list, flag_no_higher=False, flag_all_results=False, flag_lookup_genera=True, flag_format_cmnames=False):
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
            map(lambda name: "(" + vnapi.encode_b64_for_psql(name) + ")", chunk_names)
        )

        # qn, qname: query name
        sql = """
            SELECT %s
                qname,
                scname,
                LOWER(lang) AS lang_lc, 
                cmname,
                POSITION(' ' IN scname) = 0 AS flag_uninomial,
                MIN(higher.family) AS family,
                array_agg(source) AS sources, 
                array_agg(LOWER(tax_class)) OVER (PARTITION BY scname) AS agg_class, 
                array_agg(LOWER(tax_order)) OVER (PARTITION BY scname) AS agg_order, 
                array_agg(LOWER(tax_family)) OVER (PARTITION BY scname) AS agg_family, 
                COUNT(DISTINCT LOWER(source)) AS count_sources,
                array_agg(url) AS urls, 
                MAX(vnames.updated_at) AS max_updated_at, 
                MAX(source_priority) AS max_source_priority 
            FROM %s vnames RIGHT JOIN (SELECT '' AS qname UNION VALUES %s) qn
                ON 
                    LOWER(qname) = LOWER(scname) 
                    %s
                LEFT JOIN %s higher ON LOWER(SPLIT_PART(scname, ' ', 1)) = LOWER(higher.genus)
            GROUP BY 
                qname, lang_lc, scname, cmname, tax_order, tax_class, tax_family
            ORDER BY
                qname, lang_lc,
                flag_uninomial ASC,
                max_source_priority DESC, 
                count_sources DESC,
                max_updated_at DESC,
                cmname ASC
        """
        sql_query = sql.strip() % (
            # Return only the best match for each set of values.
            "DISTINCT ON (qname, lang_lc)" if not flag_all_results else "",
            access.ALL_NAMES_TABLE,
            scientificname_list,
            # If we can't find the name itself, look up the genus name.
            "OR LOWER(SPLIT_PART(qn.qname, ' ', 1)) = LOWER(scname)" if flag_lookup_genera else "",
            access.HIGHER_LIST 
        )

        # print("Sql = <<" + sql_query + ">>")
        # print("URL = <<" + access.CDB_URL % ( urllib.urlencode(dict(q=sql_query))) + ">>")

        urlresponse = urlfetch.fetch(access.CDB_URL,
            payload=urllib.urlencode(dict(
                q = sql_query
            )),
            method=urlfetch.POST,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            deadline = vnapi.DEADLINE_FETCH
        )

        if urlresponse.status_code != 200:
            raise IOError("Could not read from CartoDB: " + str(urlresponse.status_code) + ": " + str(urlresponse.content))
            
        results = json.loads(urlresponse.content)
        rows_by_scname = vnapi.groupBy(results['rows'], 'qname')

        def clean_agg(list):
            return set(filter(lambda x: x is not None and x != '', list))

        # Process each input name in this chunk, not just every resulting name.
        for query_scname in chunk_names:
            # This used to be lowercased, but we're now smart enough to keep
            # the input case unchanged, hooray!
            scname = query_scname
            
            results = []

            if scname in rows_by_scname:
                results = rows_by_scname[scname]

            results_by_lang = vnapi.groupBy(results, 'lang_lc')

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
            taxonomy['tax_family'] = set(filter(lambda x: x.lower() != scname, taxonomy['family']))
            families = filter(lambda x: x is not None, map(lambda x: x['family'], results))
            taxonomy['family'] = [families[0]] if len(families) > 0 else []

            # For every language we are interested in.
            for lang in languages_list:
                def simplify_to_list(simplify_names):
                    if flag_no_higher or len(simplify_names) == 0:
                        return set()

                    vnames = set()
                    vnresults = getVernacularNames(simplify_names, languages_list, flag_no_higher=True)

                    for simplify_name in simplify_names:
                        if not lang in vnresults[simplify_name]:
                            continue

                        vname = vnresults[simplify_name][lang].vernacularname

                        if flag_format_cmnames:
                            vnames.add(format_name(vname))
                        else:
                            vnames.add(vname)

                    return clean_agg(vnames)

                # Reactivate taxonomy common names here if you want.
                vn_tax_class = [] # simplify_to_list(taxonomy['class'])
                vn_tax_order = [] # simplify_to_list(taxonomy['order'])
                vn_agg_family = [] # simplify_to_list(taxonomy['tax_family'])
                vn_tax_family = simplify_to_list(taxonomy['family'])
                vn_vernacularname = ""
                vn_sources = set()

                vn_max_source_priority = -1
                vn_flag_uninomial = False

                vn_all_entries = []

                if (lang in results_by_lang) and (len(results_by_lang[lang]) > 0):
                    lang_results = results_by_lang[lang]

                    if flag_all_results:
                        for result in lang_results:
                            vn_all_entries.append(VernacularName(
                                scname, 
                                result['flag_uninomial'],
                                lang,
                                result['cmname'] if not flag_format_cmnames else format_name(result['cmname']),
                                result['max_source_priority'],
                                result['sources'],
                                vn_tax_class,
                                vn_tax_order,
                                vn_tax_family
                            ))
                    else:
                        vn_vernacularname = lang_results[0]['cmname']
                        vn_sources = lang_results[0]['sources']

                        vn_flag_uninomial = lang_results[0]['flag_uninomial']
                        vn_max_source_priority = lang_results[0]['max_source_priority']

                if flag_all_results:
                    best_names[lang] = []
                    for vname in vn_all_entries:
                        best_names[lang].append(VernacularName(
                            vname.scientificname,
                            vname.flag_uninomial,
                            vname.lang,
                            vname.vernacularname if not flag_format_cmnames else format_name(vnname.venacularname),
                            vname.max_source_priority,
                            vname.sources,
                            vn_tax_class,
                            vn_tax_order,
                            vn_tax_family
                        ))
                else:
                    best_names[lang] = VernacularName(
                        scname,
                        vn_flag_uninomial,
                        lang,
                        vn_vernacularname if not flag_format_cmnames else format_name(vn_vernacularname),
                        vn_max_source_priority,
                        vn_sources,
                        vn_tax_class,
                        vn_tax_order,
                        vn_tax_family
                    )

            fn_callback(query_scname, taxonomy, best_names)

