#!/usr/bin/perl -w

use v5.012;

use strict;
use warnings;

use Text::CSV;
use Try::Tiny;
use DBI;
use DBD::Pg;
use JSON;

our $FLAG_DISPLAY_LANGUAGES = 0;
our $FLAG_DISPLAY_ORDERS = 0;

my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432", 
    'vaidyagi', '', 
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

# We want to display all the results split up in the following way:
#   - source
#       - class
#           - order
#               - lang  -> (number of distinct scientific names)
my $results = $db->selectall_arrayref(
    "SELECT source, tax_class, tax_order, lang, scname, COUNT(*) " .
        "FROM entries " .
        "GROUP BY source, tax_class, tax_order, lang, scname;",
);

my %vernacular_names;

foreach my $row (@$results) {
    my $source = $row->[0];
    my $tax_class = $row->[1];
    my $tax_order = $row->[2];
    my $lang = $row->[3];
    my $scname = $row->[4];
    my $count = $row->[5];

    $vernacular_names{$source}{$tax_class}{$tax_order}{$lang}{$scname} = 0
        unless exists $vernacular_names{$source}{$tax_class}{$tax_order}{$lang}{$scname};

    $vernacular_names{$source}{$tax_class}{$tax_order}{$lang}{$scname} += $count;
}

say STDERR "Database loaded.";

foreach my $source (sort keys %vernacular_names) {
    my $classes = $vernacular_names{$source};
    
    say "Source: $source";

    foreach my $tax_class (sort keys %$classes) {
        my $orders = $classes->{$tax_class};

        my $count_class_all_species = 0;
        my $count_class_with_1_common_name = 0;

        my $class_summary = "";

        foreach my $tax_order (sort keys %$orders) {
            my $languages = $orders->{$tax_order};

            # List all species.
            my %species_with_1_common_name = ();

            # Count all species.
            my $count_all_scnames = scalar keys (%{$languages->{'la'}});
            $count_class_all_species += $count_all_scnames;

            my $lang_count = 0;
            my $language_summary = "";
            my @order_percentages = ();

            foreach my $lang (sort keys %$languages) {
                my $scnames = $languages->{$lang};

                $lang_count++;
                my $count_scnames = 0;
                my $count_cmnames = 0;

                foreach my $scname (sort keys %$scnames) {
                    $count_scnames++;
                    $count_cmnames += $scnames->{$scname};
                
                    # Add to @species_with_1_common_name
                    $species_with_1_common_name{$scname} = 1
                        unless $lang eq 'la';
                }

                # Report findings.
                my $percentage = ($count_scnames/$count_all_scnames * 100);
                my $percentage_str = percentage_str($percentage);

                $language_summary .= "        $lang: $count_scnames ($count_cmnames common names) out of $count_all_scnames ($percentage_str)\n" if $FLAG_DISPLAY_LANGUAGES;

                # Skip latin in the summaries.
                push @order_percentages, $percentage unless $lang eq 'la';
            }

            $count_class_with_1_common_name += scalar keys %species_with_1_common_name;

            $tax_order = "(none)" if $tax_order eq '';
            $class_summary .= 
                "    Order: $tax_order ($lang_count languages, $count_all_scnames species): " . 
                summarize_percentage(@order_percentages) . "\n" .
                $language_summary
                if $FLAG_DISPLAY_ORDERS;
        }

        say "  Class: $tax_class " . 
            "($count_class_with_1_common_name/$count_class_all_species = " .
            percentage_str($count_class_with_1_common_name/$count_class_all_species*100) .
            ")"
        ;
        print $class_summary;
    }

}

sub percentage_str {
    my $perc = shift;

    $perc = 0 unless defined $perc;

    return sprintf "%.2f%%", $perc;
}

use Statistics::Descriptive;

sub summarize_percentage {
    my @percentages = @_;

    my $stat = Statistics::Descriptive::Full->new();
    $stat->add_data(@percentages);

    return "min: " . percentage_str($stat->min()) .
        " median: " . percentage_str($stat->median()) .
        " mean: " . percentage_str($stat->mean()) .
        " max: " . percentage_str($stat->max())
    ;
}
