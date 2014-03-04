#!/usr/bin/perl -w

# Add the shortened names I ran separately through EOL.

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

open(my $fh, "<:encoding(utf8)", "data/after-shortening-to-binomial/results-scname_in_entries_with_distinct_binomials.csv") or die "Could not open STDIN: $!";
$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $scname = $row->{'scname'};
    my $binomial = $row->{'binomial'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_common_names_json'};

    next if $scname eq '';

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "Truncated names; EOL API calls on March 4, 2014";
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
                "(scname, binomial, cmname, lang, source, url, source_url) " .
                "VALUES (?, ?, ?, ?, ?, ?, ?);",
                {}, 
                $scname, $binomial, $cmname, $lang, $source, $url, $source_url
            );
            # say "$scname ($binomial) -> $cmname in $lang ($url)";
        }
    
    } catch {
        chomp;

        my $header = (split "\n", $json)[0];
        say STDERR "$scname ($binomial): $header\n\t<<$_>>";
    };

}


close($fh);
