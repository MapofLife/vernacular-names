# vim: set fileencoding=utf-8 : 

"""names.py: Functions for working with vernacular names."""

import logging
import json

from nomdb import config, common, languages
import access

# There are only two use-cases for vernacular name retrieval:
# 1. /edit, which needs every possible name in every possible language with higher taxonomy.
#   get_detailed_vname(scname, ...) -> dict[vnames]
# 2. The currently accepted name in the standard languages for a set of names.
#   get_vname(scnames, ...) -> dict[vnames]
# and hey, because polymorphism is great, right
#   get_vname(scname, ...) -> vname
#
# In theory, we could make things faster by looking up scientific names within a dataset directly,
# but in practice we don't do that often. Maybe if I write it, they will come?

def get_vname(scname):
    return get_detailed_vname(scname)

def get_vnames(list_scnames):
    """

    :param scnames: list[str]
    :return: dict
    """

    # Set up results and names to query.
    final_results = dict()
    scnames = list(set(list_scnames))

    # Prepare a list of languages to use.
    languages_str = ", ".join(map(lambda lang: "'" + lang.lower() + "'", languages.language_names_list))

    # We go through names in chunks, so we don't overwhelm CartoDB on long queries.
    for i in xrange(0, len(scnames), config.SEARCH_CHUNK_SIZE):
        chunk_scnames = scnames[i:i+config.SEARCH_CHUNK_SIZE]

        # Turn them into a search string.
        set_scnames = set(chunk_scnames)

        # We might also want to search for genus names.
        for name in chunk_scnames:
            pieces = name.split()
            if len(pieces) > 1:
                set_scnames.add(pieces[0].lower())

        # print("DEBUG: vnames = " + ', '.join(set_scnames))

        scnames_str = ", ".join(set(
            map(lambda name: "(" + common.encode_b64_for_psql(name.lower()) + ")", set_scnames)
        ))

        sql = """
            SELECT DISTINCT
                qname,
                LOWER(scname) AS scname_lc,
                LOWER(lang) AS lang_lc,
                FIRST_VALUE(cmname) OVER best_match AS cmname,
                FIRST_VALUE(source) OVER best_match AS source,
                FIRST_VALUE(source_priority) OVER best_match AS source_priority,
                FIRST_VALUE(url) OVER best_match AS url,
                FIRST_VALUE(source_url) OVER best_match AS source_url,
                FIRST_VALUE(updated_at) OVER best_match AS updated_at
            FROM %s vnames
                RIGHT JOIN (SELECT NULL AS qname UNION VALUES %s) qn
                    ON LOWER(scname) = qname
            WHERE
                qname IS NOT NULL AND (
                    cmname IS NULL OR
                    LOWER(lang) IN (%s)
                )
            WINDOW best_match AS (
                PARTITION BY LOWER(scname), lang ORDER BY
                    source_priority DESC,
                    updated_at DESC
            )
            ORDER BY
                qname ASC,
                scname_lc ASC,
                cmname ASC
        """
        sql_query = sql.strip() % (
            access.ALL_NAMES_TABLE,
            scnames_str,
            languages_str
        )

        # print("Sql = <<" + sql_query + ">>")
        # print("URL = <<" + access.CDB_URL % ( urllib.urlencode(dict(q=sql_query))) + ">>")

        urlresponse = common.url_post(access.CDB_URL, {'q': sql_query})

        if urlresponse.status_code != 200:
            raise IOError("Could not read from CartoDB: " + str(urlresponse.status_code) + ": " + str(urlresponse.content))

        results = json.loads(urlresponse.content)
        rows_by_scname = common.group_by(results['rows'], 'qname')

        # Map qnames back to scnames
        for scname in chunk_scnames:
            final_results[scname] = dict()

            try:
                rows_by_lang = common.group_by(rows_by_scname[scname.lower()], 'lang_lc')
            except KeyError:
                # This only gets triggered if we have a vname for 'scname', but
                # not as one of the requested languages.
                for lang in languages.language_names_list:
                    final_results[scname][lang] = None

                continue

            for lang in languages.language_names_list:
                if lang not in rows_by_lang:
                    final_results[scname][lang] = None
                    continue

                rows = rows_by_lang[lang]

                # There should only be one row!
                if len(rows) != 1:
                    raise RuntimeError(
                        "SQL query should only return one row per scientific name, but we have %d of '%s'@%s" % (
                            len(rows),
                            scname,
                            lang
                        )
                    )

                # ... but it might be blank.
                result = rows[0]

                if result['cmname'] is None:
                    # Maybe we have a genus match?
                    pieces = scname.split()
                    if len(pieces) > 1 and len(rows_by_scname[pieces[0].lower()]) == 1:
                        result = rows_by_scname[pieces[0].lower()][0]
                    else:
                        final_results[scname][lang] = None
                        continue

                final_results[scname][lang] = VernacularName(
                    scname,
                    result['qname'],
                    result['lang_lc'],
                    result['cmname'],
                    result['source'],
                    result['source_priority'] if result['source_priority'] is not None else config.PRIORITY_DEFAULT,
                    result['url'],
                    result['source_url'],
                    result['updated_at']
                )

    return final_results

def get_detailed_vname(scname):
    raise RuntimeError("Not implemented")

#
# CLASS VernacularName
#
class VernacularName:
    """ Stores a single vernacular name in a particular language. """
    def __init__(self, scientific_name, matched_name, lang, vernacular_name, source, source_priority, url, source_url, updated_at):
        """ Create a VernacularName
        
        :param scientific_name: The scientific name that was queried (not necessarily the one that was matched!)
        :param matched_name: The name the vernacular_name corresponds to.
        :param lang: The language code ('en', 'zh-Hans', etc.)
        :param vernacular_name: The vernacular name retrieved.
        :param source_priority: The source priority of the match.
        :param source: The source that was matched.
        :return:
        """
        self.scname = scientific_name
        self.matched_name = matched_name
        self.lang = lang
        self.cmname = vernacular_name
        self.source = source
        self.source_priority = int(source_priority)
        if not(config.PRIORITY_MIN <= self.source_priority <= config.PRIORITY_MAX):
            self.source_priority = config.PRIORITY_DEFAULT
        self.url = url
        self.source_url = source_url
        self.updated_at = updated_at

    def __repr__(self):
        """For debugging and the like."""
        return "<'%s'@%s>" % (
            self.cmname,
            self.lang
        )

    @property
    def scientific_name(self):
        return self.scname

    @property
    def matched_name(self):
        return self.matched_name

    @property
    def lang(self):
        return self.lang

    @property
    def vernacular_name(self):
        return self.cmname

    @property
    def vernacular_name_formatted(self):
        return common.format_name(self.cmname) if self.cmname is not None else None

    @property
    def source_priority(self):
        return self.source_priority

    @property
    def source(self):
        return self.source

    @property
    def url(self):
        return self.url

    @property
    def source_url(self):
        return self.source_url

    @property
    def updated_at(self):
        return self.updated_at

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
    for chunk_names in chunks(query_names_sorted, config.SEARCH_CHUNK_SIZE):
        row += len(chunk_names)

        # Only display progress on the main task.
        if row >= 10:
            logging.info("Downloaded %d rows of %d names." % (row, len(query_names_sorted)))

        # Get higher taxonomy, language, common name.
        # TODO: fallback to trinomial names (where we have Panthera tigris tigris but not Panthera tigris.
        scientificname_list = ", ".join(
            map(lambda name: "(" + common.encode_b64_for_psql(name) + ")", chunk_names)
        )

        # qn, qname: query name
        sql = """
            SELECT %s
                qname,
                scname,
                LOWER(lang) AS lang_lc, 
                cmname,
                POSITION(' ' IN scname) = 0 AS flag_uninomial,
                array_agg(master_list.family) AS ml_agg_family,
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
                LEFT JOIN %s master_list 
                ON 
                    LOWER(qname) = LOWER(master_list.scientificname)
                    OR LOWER(qname) = LOWER(SPLIT_PART(master_list.scientificname, ' ', 1))
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
            access.MASTER_LIST
        )

        # print("Sql = <<" + sql_query + ">>")
        # print("URL = <<" + access.CDB_URL % ( urllib.urlencode(dict(q=sql_query))) + ">>")

        urlresponse = common.url_post(access.CDB_URL,
            data=dict(
                q = sql_query
            )
        )

        if urlresponse.status_code != 200:
            raise IOError("Could not read from CartoDB: " + str(urlresponse.status_code) + ": " + str(urlresponse.content))
            
        results = json.loads(urlresponse.content)
        rows_by_scname = common.group_by(results['rows'], 'qname')

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

            results_by_lang = common.group_by(results, 'lang_lc')

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
                    taxonomy['family'].update(clean_agg(result['ml_agg_family']))

            # Prevent recursion: remove any higher taxonomy
            # that is part of our current query.
            taxonomy['class'] = set(filter(lambda x: x.lower() != scname, taxonomy['class']))
            taxonomy['order'] = set(filter(lambda x: x.lower() != scname, taxonomy['order']))
            taxonomy['family'] = set(filter(lambda x: x.lower() != scname, taxonomy['family']))

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

                        vname = vnresults[simplify_name][lang].vernacular_name

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
                            vn_all_entries.append(VernacularName(scname, result['flag_uninomial'], lang, result[
                                'cmname'] if not flag_format_cmnames else format_name(result['cmname']),
                                                                 result['sources'], result['max_source_priority']))
                    else:
                        vn_vernacularname = lang_results[0]['cmname']
                        vn_sources = lang_results[0]['sources']

                        vn_flag_uninomial = lang_results[0]['flag_uninomial']
                        vn_max_source_priority = lang_results[0]['max_source_priority']

                if flag_all_results:
                    best_names[lang] = []
                    for vname in vn_all_entries:
                        best_names[lang].append(VernacularName(vname.scientific_name, vname.flag_genus_match, vname.lang,
                                                               vname.vernacular_name if not flag_format_cmnames else format_name(
                                                                   vname.venacularname), vname.sources,
                                                               vname.max_source_priority))
                else:
                    best_names[lang] = VernacularName(scname, vn_flag_uninomial, lang,
                                                      vn_vernacularname if not flag_format_cmnames else format_name(
                                                          vn_vernacularname), vn_sources, vn_max_source_priority)

            fn_callback(query_scname, taxonomy, best_names)

