#!/usr/bin/perl -w

# This script is designed to add families (and other higher taxonomy)
# from Map of Life's 'taxonomy' table. We'll put in the English common
# names while we're at it, too.

use v5.012;

use strict;
use warnings;

use Try::Tiny;
use Text::CSV;
use DBI;
use DBD::Pg;
use JSON;

my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432", 
    'vaidyagi', '', 
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

my $csv = Text::CSV->new({ 
    binary => 1,
    eol => $/
})
    or die "CSV failed";

open(my $fh, "<:encoding(utf8)", "data/mol.cartodb.com-taxonomy.csv") or die "Could not open STDIN: $!";
$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $canonicalName = $row->{'scientificname'};
    my $en_name = $row->{'common_names_eng'};
    my $tax_class = $row->{'class'};
    my $tax_order = $row->{'_order'};
    my $tax_family = $row->{'family'};
    my $url = "https://mol.cartodb.com/tables/taxonomy/#row_" . $row->{'cartodb_id'};

    # Some standard ones.
    my $source = "Map of Life 'taxonomy' table as of February 28, 2014";
    my $source_url = "http://mappinglife.org/";

    # Display.
    # say "$canonicalName: $en_name (class $tax_class > order $tax_order > family $tax_family): $url";

    # Now add them into the database.
    $db->do("INSERT INTO entries " .
        "(scname, cmname, lang, url, source, source_url, tax_class, tax_order, tax_family) " .
        "VALUES (?, ?, 'en', ?, ?, ?, ?, ?, ?);",
        {}, 
        $canonicalName, $en_name, $url, $source, $source_url,
        $tax_class, $tax_order, $tax_family
    );
}

close($fh);
