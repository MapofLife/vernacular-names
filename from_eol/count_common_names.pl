#!/usr/bin/perl -w

use v5.012;

use strict;
use warnings;

use Try::Tiny;
use Text::CSV;
use DBI;
use DBD::Pg;
use JSON;

my $csv = Text::CSV->new({ 
    binary => 1,
    eol => $/
})
    or die "CSV failed";

open(my $fh, "<:encoding(utf8)", "data/eol_results_from_openrefine.csv") or die "Could not open STDIN: $!";
$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $canonicalName = $row->{'canonicalName'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_common_names_and_synonyms'};
    my $tax_order = $row->{'order'};
    my $tax_class = $row->{'class'};

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "EOL API calls week of February 9 to 15, 2014";
    my $source_url = "http://eol.org/api/";

    say $canonicalName;
}


close($fh);
