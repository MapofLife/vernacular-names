# vim: set fileencoding=utf-8 :

#
# generate_tt.py: Generate taxonomy_translations table.
# This file is now defunct: we use Luis' script at
# https://github.com/MapofLife/database/blob/master/scripts_autopopulate/taxonomy_translations.py
#


import gzip
import csv
import os
import os.path
import time
import sys

sys.path.append('config')
sys.path.append('lib/urlfetch')

import languages
from nomdb import masterlist
import vnnames

# Configuration settings.
OUTPUT_PATH = 'results/'

# Measure time taken for extract.
time_started = time.time()

# Create file
csv_filename = "taxonomy_translations_" + time.strftime("%Y_%B_%d_%H%MZ", time.gmtime())  + ".csv.gz"
if os.path.isfile(OUTPUT_PATH + csv_filename):
    print("File '" + csv_filename + "' already exists!")
    sys.exit(1)

# Create file in memory and make it gzipped.
gzfile = gzip.GzipFile(filename=OUTPUT_PATH + csv_filename, mode='wb')

# Prepare csv writer.
csvfile = csv.writer(gzfile)

# Get a list of every name in the master list.
all_names = masterlist.getMasterList()

# Prepare to write out CSV.
header = ['scientificname', 'tax_family', 'tax_order', 'tax_class']
for lang in languages.language_names_list:
    header.extend([lang + '_name', lang + '_source', lang + '_family'])
header.extend(['empty'])
csvfile.writerow(header)

def concat_names(names):
    return "|".join(sorted(names)).encode('utf-8')

rowcount = 0
def add_name(name, higher_taxonomy, vnames_by_lang):
    global rowcount
    rowcount += 1

    if rowcount % 1000 == 0:
        print("Adding row " + str(rowcount))

    row = [name.encode('utf-8').capitalize(), 
        concat_names(higher_taxonomy['family']),
        concat_names(higher_taxonomy['order']),
        concat_names(higher_taxonomy['class'])]

    for lang in languages.language_names_list:
        if lang in vnames_by_lang:
            vname = vnames_by_lang[lang].vernacularname
            sources = vnames_by_lang[lang].sources
            tax_family = vnames_by_lang[lang].tax_family

            # Use family latin name instead of common name 
            # if we don't have one.
            if len(tax_family) == 0:
                tax_family = map(lambda x: x.capitalize(), higher_taxonomy['family'])

            row.extend([
                vname.encode('utf-8'), 
                concat_names(sources),
                concat_names(list(tax_family)[0:1])   # Only enter a single family name.
            ])
        else:
            row.extend([None, None, None])

    csvfile.writerow(row)

# searchVernacularNames doesn't use the cache, but it calls 
# getVernacularNames for higher taxonomy, which does.
vnnames.clearVernacularNamesCache()
vnnames.searchVernacularNames(add_name, all_names, languages.language_names_list, flag_format_cmnames=True)

# File completed!
gzfile.close()

time_ended = time.time()

print(str(rowcount) + " rows written in " + str(time_ended-time_started) + " seconds")
