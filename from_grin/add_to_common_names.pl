#!/usr/bin/perl -w

use v5.012;

use strict;
use warnings;

use Try::Tiny;
use Text::CSV;
use DBI;
use DBD::Pg;
use JSON;

# FLAG_DEBUG = 1 turns off writing to the database. 
our $FLAG_DEBUG = 0;

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

open(my $fh, "<:encoding(utf8)", "data/grin_common_2014jun26.csv") 
    or die "Could not open STDIN: $!";

binmode STDOUT, "encoding(utf8)";

$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $canonicalName = $row->{'scname'};
    my $acceptedName = $row->{'acname'};
    my $grin_taxon_id = $row->{'TAXNO'};

    my $vernacular_name = $row->{'vernacularName'};
    my $lang = $row->{'lang'};

    # Some standard ones.
    my $url = "http://www.ars-grin.gov/cgi-bin/npgs/html/taxon.pl?$grin_taxon_id";
    my $source = "ARS GRIN taxonomy download on June 26, 2014";
    my $source_url = "http://www.ars-grin.gov/cgi-bin/npgs/html/index.pl";

    # Add the vernacular name to the database.
    if($FLAG_DEBUG) {
        say "$canonicalName -> '$vernacular_name' ($lang) from $source: $url";
        say " (also) $acceptedName (from $canonicalName) -> '$vernacular_name' ($lang)"
            if $acceptedName ne $canonicalName;
    } else {
        $db->do("INSERT INTO entries " .
            "(scname, cmname, lang, source, url, source_url) " .
            "VALUES (?, ?, ?, ?, ?, ?);",
            {}, 
            $canonicalName, $vernacular_name, $lang, $source, $url, $source_url
        );

        if($acceptedName ne $canonicalName) {
            $db->do("INSERT INTO entries " .
                "(scname, cmname, lang, source, url, source_url) " .
                "VALUES (?, ?, ?, ?, ?, ?);",
                {}, 
                $acceptedName, $vernacular_name, $lang, $source, $url, $source_url
            );
        }
    }

}


close($fh);
