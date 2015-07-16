# vim: set fileencoding=utf-8 :

#
# generate_tt.py: Generate taxonomy_translations table.
# This file is now semi-defunct: we use Luis' script at
# https://github.com/MapofLife/database/blob/master/scripts_autopopulate/taxonomy_translations.py
# EXCEPT that -- since it's PostgreSQL-only -- it can't handle proper capitalization,
# or at any rate the amount of capitalization that we can. So we need to remain functional
# for the time being.
#


import gzip
import csv
import os
import os.path
import time
import datetime
import sys

sys.path.append('config')
sys.path.append('lib/urlfetch')
sys.path.append('lib/python-titlecase')

from nomdb import masterlist, names, languages

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

# Prepare to write out CSV.
header = ['scientificname', 'tax_family']
for lang in languages.language_names_list:
    header.extend([lang + '_name', lang + '_source', lang + '_family'])
header.extend(['empty'])
csvfile.writerow(header)

def concat_names(names):
    return "|".join(sorted(names)).encode('utf-8')

# Set up lookup.
rowcount = 0

# Get a list of every name in the master list.
all_names = masterlist.get_master_list()

# Look up higher taxonomy.
print("Looking up higher taxonomy ... ")
# TODO: Given that this is a slow-running SQL request, it might be easier to download the CSV and parse that instead.
higher_taxonomy = masterlist.get_higher_taxonomy(all_names)
print("Higher taxonomy retrieved for %d names." % len(higher_taxonomy))

print("Looking up vernacular names for higher taxonomy ... ")
tax_families = names.get_vnames(map(lambda entry: entry['family'].lower(), higher_taxonomy.itervalues()))
print("Vernacular names downloaded for %d families." % len(tax_families))

# Break the request up into chunks.
CHUNK_SIZE = 3000
time_start = time.time()
for i in xrange(0, len(all_names), CHUNK_SIZE):
    chunk_scnames = all_names[i:i + CHUNK_SIZE]

    vnames_chunk = names.get_vnames(chunk_scnames)
    print("Retrieved vernacular names for %d scientific names, %d rows of %d (%.2f%%) written so far." % (
        len(vnames_chunk), rowcount, len(all_names), float(rowcount)/len(all_names) * 100))

    # Estimate time left.
    time_elapsed = time.time() - time_start
    time_remaining = len(all_names)/(rowcount/time_elapsed) - time_elapsed if rowcount > 0 else 0
    print("Time elapsed: %s, time remaining: %s" % (
        str(datetime.timedelta(seconds=time_elapsed)),
        str(datetime.timedelta(seconds=time_remaining))
    ))

    for name in chunk_scnames:
        rowcount += 1
        vnames = vnames_chunk[name]
        family_name = higher_taxonomy[name.lower()]['family']
        family_vnames = tax_families[family_name.lower()]

        row = [name.encode('utf-8').capitalize(), family_name.encode('utf-8').capitalize()]

        for lang in languages.language_names_list:
            if lang in vnames:
                vname = vnames[lang].vernacular_name_formatted.encode('utf-8') if lang in vnames and vnames[lang] is not None else None
                source = vnames[lang].source.encode('utf-8') if lang in vnames and vnames[lang] is not None else None
                tax_family = family_vnames[lang].vernacular_name_formatted.encode('utf-8') if lang in family_vnames and family_vnames[lang] is not None else None

                # Use family latin name instead of common name
                # if we don't have one.
                if tax_family is None or tax_family == '':
                    tax_family = family_name.encode('utf-8').capitalize()

                row.extend([
                    vname,
                    source,
                    tax_family
                ])
            else:
                row.extend([None, None, None])

        csvfile.writerow(row)

# File completed!
gzfile.close()

time_ended = time.time()

print(str(rowcount) + " rows written in " + str(datetime.timedelta(seconds=time_ended-time_started)))
