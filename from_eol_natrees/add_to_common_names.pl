#!/usr/bin/perl -w

use v5.012;

use strict;
use warnings;

use Try::Tiny;
use Text::CSV;
use DBI;
use DBD::Pg;
use JSON;
use Data::Dumper;

# If = 1: no writes to database, only write to STDOUT.
our $FLAG_DEBUG_ONLY = 0;

my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432", 
    'vaidyagi', '', 
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

my $csv = Text::CSV->new({ 
    binary => 1,
#    quote_char => undef,
#    escape_char => undef,
#    sep_char => "\t",
    eol => $/
})
    or die "CSV failed";

open(my $fh, "<:encoding(utf8)", "data/na_trees_uniq_2014may2_with_higher.csv")
    or die "Could not open input: $!";
$csv->column_names($csv->getline($fh));

say STDERR "column names: " . join(", ", $csv->column_names);

my $row_count = 0;
while(my $row = $csv->getline_hr($fh)) {
    $row_count++;

    # Fields we're interested in.
    my $canonicalName = $row->{'latin'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_synonyms_and_vern'};

    # Fix JSON.
    #$json =~ s/^\s*"//g;
    #$json =~ s/"\s*$//g;
    #$json =~ s/""/"/g;

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "EOL API calls, May 2, 2014: na_trees";
    my $source_url = "http://eol.org/api/";
    my $tax_order;
    my $tax_class;

    # Add the name into the database using the language 'la'. We need to do
    # this, since none of these names are in the current, tetrapod-only db.
    if($FLAG_DEBUG_ONLY) {
        say STDERR "\n\n$row_count.\n\nAdding la name: $canonicalName from $source_url";
    } else {
        $db->do("INSERT INTO entries " .
            "(scname, cmname, lang, source, url, source_url) " .
            "VALUES (?, ?, ?, ?, ?, ?);",
            {}, 
            $canonicalName, $canonicalName, "la", $source, $url, $source_url
        );
    }

    # Try to parse the JSON.
    if($json ne '') {
    try {
        my $eol_results = decode_json($json);

        # Look up higher taxonomy.
        my %higher_taxonomy;

        try {
            my $taxonHierarchy = decode_json($row->{'eol_taxon_hierarchy'});
            my $ancestors = $taxonHierarchy->{'ancestors'};

            foreach my $ancestor (@$ancestors) {
                $higher_taxonomy{lc $ancestor->{'taxonRank'}} = lc $ancestor->{'scientificName'};
            }

        } catch {
            warn "Could not parse taxon hierarchy: <<" . $row->{'eol_taxon_hierarchy'} . ">>";
        };

        # Store some higher taxonomy.
        my $tax_kingdom = $higher_taxonomy{'kingdom'};
        my $tax_phylum = $higher_taxonomy{'phylum'};
        $tax_class = $higher_taxonomy{'class'};
        $tax_order = $higher_taxonomy{'order'};
        my $tax_family = $higher_taxonomy{'family'};
        my $tax_genus = $higher_taxonomy{'genus'};

        my $commonNames = $eol_results->{'vernacularNames'};

        foreach my $commonName (@$commonNames) {
            my $cmname = $commonName->{'vernacularName'};
            my $lang = $commonName->{'language'};

            # Now add them into the database.
            if($FLAG_DEBUG_ONLY) {
                say STDERR "  Adding $lang to $canonicalName: $cmname from $source_url";
            } else {
                $db->do("INSERT INTO entries " .
                    "(scname, cmname, lang, source, url, source_url, tax_kingdom, tax_phylum, tax_class, tax_order, tax_family, tax_genus) " .
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    {}, 
                    $canonicalName, $cmname, $lang, $source, $url, $source_url,
                    $tax_kingdom, $tax_phylum, $tax_class, $tax_order, $tax_family, $tax_genus
                );
            }
        }
    
    } catch {
        chomp;

        my $header = (split "\n", $json)[0];
        say STDERR "$canonicalName ($tax_order|$tax_class): $header\n\t<<$_>>";
    };
    }

}


close($fh);
