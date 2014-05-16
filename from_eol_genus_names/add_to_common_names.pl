#/usr/bin/perl -w

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

open(my $fh, "<:encoding(utf8)", "data/genera_with_higher_2014may14.csv")
    or die "Could not open input: $!";
$csv->column_names($csv->getline($fh));

say STDERR "column names: " . join(", ", $csv->column_names);

my $row_count = 0;
while(my $row = $csv->getline_hr($fh)) {
    $row_count++;

    # Fields we're interested in.
    my $canonicalName = $row->{'genus'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_synonyms_and_vern'};

    # Fix JSON.
    #$json =~ s/^\s*"//g;
    #$json =~ s/"\s*$//g;
    #$json =~ s/""/"/g;

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "EOL API calls, May 14, 2014: all genera";
    my $source_url = "http://eol.org/api/";
    my $tax_kingdom = $row->{'tax_kingdom'};
    my $tax_phylum = $row->{'tax_phylum'};
    my $tax_class = $row->{'tax_class'};
    my $tax_order = $row->{'tax_order'};
    my $tax_family = $row->{'tax_family'};
    my $tax_genus = $row->{'tax_genus'};

    # Try to parse the JSON.
    if($json ne '') {
    try {
        my $eol_results = decode_json($json);
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
