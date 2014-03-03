#!/usr/bin/perl -w

=head1 NAME

add_to_common_names.pl - load names from Walter's list and load them into the COmmon Names Framework

=cut

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
    eol => $/,
    sep_char => "\t"
})
    or die "CSV failed";

open(my $fh, "<:encoding(utf8)", "data/voewelt-utf8.txt") or die "Could not open STDIN: $!";
$csv->column_names($csv->getline($fh));

while(my $row = $csv->getline_hr($fh)) {
    # Fields we're interested in.
    my $canonicalName = $row->{'latname'};
    my $en_name = $row->{'englname'};
    my $en_name2 = $row->{'engl2name'};
    my $de_name = ($row->{'dtspec'} // "") . ($row->{'dtgat'} // "");
    my $de_name2 = ($row->{'dt2spec'} // "") . ($row->{'dt2gat'} // "");

    my $genus = $row->{'latgat'};
    my $tax_class = "Aves";
    
    # Some standard ones.
    my $source = "Walter's authoritative German name XLS file, December 16, 2014";
    my $source_url = "https://basecamp.com/1756489/projects/1391223-new-datasets/messages/17452025-multilingual#comment_117050917";

    # Add the name into the database using the language 'la'.
    $db->do("INSERT INTO entries " .
        "(scname, cmname, lang, source, source_url, tax_class, tax_genus) " .
        "VALUES (?, ?, ?, ?, ?, ?, ?);",
        {}, 
        $canonicalName, $canonicalName, "la", $source, $source_url,
        $tax_class, $genus
    );

    # Add English names.
    my @en_names;
    push @en_names, $en_name if (defined $en_name) and ($en_name ne "");
    push @en_names, $en_name2 if (defined $en_name2) and ($en_name2 ne "");

    foreach my $cmname (@en_names) {
        # Now add them into the database.
        $db->do("INSERT INTO entries " .
            "(scname, cmname, lang, source, source_url, tax_class, tax_genus) " .
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            {}, 
            $canonicalName, $cmname, "en", $source, $source_url,
            $tax_class, $genus
        );
    }

    # Add German names.
    my @de_names;
    push @de_names, $de_name if (defined $de_name) and ($de_name ne "");
    push @de_names, $de_name2 if (defined $de_name2) and ($de_name2 ne "");

    foreach my $cmname (@de_names) {
        # Now add them into the database.
        $db->do("INSERT INTO entries " .
            "(scname, cmname, lang, source, source_url, tax_class, tax_genus) " .
            "VALUES (?, ?, ?, ?, ?, ?, ?);",
            {}, 
            $canonicalName, $cmname, "de", $source, $source_url,
            $tax_class, $genus
        );
    }
}


close($fh);
