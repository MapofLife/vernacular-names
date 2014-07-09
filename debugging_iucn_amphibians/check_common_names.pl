#!/usr/bin/perl -w

=head1 NAME

check_common_names.pl - Check common names

=cut

use v5.010;

use strict;
use warnings;

use DBI;
use DBD::Pg;
use Try::Tiny;
use JSON;
use Text::CSV;

# Options
our @LANGUAGES = (
    'la',
    'en',
    'fr',
    'de',
    'es',
    'pt',
    'zh'
);

# Start counting.
my $start_time = time;

# Set up database connection.
my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432",
    'vaidyagi', '',
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1,
    pg_enable_utf8 => 1
});

# Prepare CSV output.
use Text::CSV;
my $csv = Text::CSV->new ( { binary => 1, allow_whitespace => 1 } );
my @columns = ('scname');
push @columns, $_ foreach @LANGUAGES;

$csv->column_names(@columns);
binmode(STDOUT, ":utf8");
$csv->print(*STDOUT, \@columns);
say "";

# Look up a name in a particular language.
my $languages = join(', ', map { "'$_'" } @LANGUAGES); 
my $sth = $db->prepare("SELECT cmname, LOWER(lang) AS lang_lower, COUNT(*) AS count_cmname,"
        . " array_agg(source) AS sources, "
        . " array_agg(tax_kingdom),"
        . " array_agg(tax_phylum),"
        . " array_agg(tax_class),"
        . " array_agg(tax_order),"
        . " array_agg(tax_family),"
        . " array_agg(tax_genus),"
        . " array_agg(genus)"
        . " FROM entries"
        . " WHERE LOWER(binomial)=? AND LOWER(lang) IN ($languages) AND source NOT LIKE 'GBIF%'"
        . " GROUP BY cmname, lang_lower"
        . " ORDER BY count_cmname DESC, lang_lower DESC"
    );

# Read binomials from the command line.
while(<>) {
    chomp;

    my $scname = $_;
    my @results = ($scname);

    $sth->execute(lc $scname);
    my $rows = $sth->fetchall_hashref("lang_lower"); 

    for my $lang (@LANGUAGES) {
        push @results, $rows->{lc $lang}->{'cmname'};
    }

    $csv->print(*STDOUT, \@results);
    say "";
}

