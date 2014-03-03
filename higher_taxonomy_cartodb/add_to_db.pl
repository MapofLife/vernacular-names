#!/usr/bin/perl -w

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

open(my $fh, "<:encoding(utf8)", "data/mol-cartodb-com-taxonomy-higher-taxonomy.csv") or die "Could not open STDIN: $!";
$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $canonicalName = $row->{'name'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_common_names_json'};

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "EOL API calls on February 28, 2014";
    my $source_url = "http://eol.org/api/";

    # Try to parse the JSON.
    try {
        my $eol_results = decode_json($json);

        my $commonNames = $eol_results->{'vernacularNames'};

        foreach my $commonName (@$commonNames) {
            my $cmname = $commonName->{'vernacularName'};
            my $lang = $commonName->{'language'};

            # Now add them into the database.
            $db->do("INSERT INTO entries " .
                "(scname, cmname, lang, source, url, source_url) " .
                "VALUES (?, ?, ?, ?, ?, ?);",
                {}, 
                lc($canonicalName), $cmname, $lang, $source, $url, $source_url
            );
                
            #say "$canonicalName: $cmname in $lang";
        }
    
    } catch {
        chomp;

        my $header = (split "\n", $json)[0];
        say STDERR "$canonicalName: $header\n\t<<$_>>";
    };

}


close($fh);
