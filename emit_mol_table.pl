#!/usr/bin/perl -w

=head1 NAME

create_mol_table.pl - Create a table of common names for Map of Life

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
our $FLAG_CONCAT_ALL = 0;
    # 0 = pick the most popular name for each language
    # 1 = concatenate all the names of each language
our $FLAG_PRINT_SOURCE = 1;
    # 0 = don't print sources
    # 1 = print sources
our @LANGUAGES = (
    'en',
    'fr',
    'de',
    'es',
    'hi',
    'pt',
    'zh'
);

# Set up results folder.
mkdir("results");
our $OUTPUT_DIR = "results/" . time;
die "Results already exist!" if -e $OUTPUT_DIR;
mkdir($OUTPUT_DIR);

open(my $fh_table, ">:encoding(utf8)", "$OUTPUT_DIR/mol_table.csv")
    or die "Could not create '$OUTPUT_DIR/mol_table.csv': $!";

open(my $fh_summary, ">:encoding(utf8)", "$OUTPUT_DIR/mol_summary.csv")
    or die "Could not create '$OUTPUT_DIR/mol_summary.csv': $!";

open(my $fh_missing, ">:encoding(utf8)", "$OUTPUT_DIR/mol_missing.csv")
    or die "Could not create '$OUTPUT_DIR/mol_missing.csv': $!";

# Set up database connection.
my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432",
    'vaidyagi', '',
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

# Retrieve a list of all species containing names in any of our languages.
my $languages = join(', ', map { "'$_'" } @LANGUAGES);
my $sth = $db->prepare("SELECT scname FROM entries WHERE LOWER(lang) IN ($languages) AND source NOT LIKE 'GBIF%' GROUP BY scname");
$sth->execute;
my $scientific_names = $sth->fetchall_arrayref([0]);

# Prepare CSV output.
use Text::CSV;
my $csv = Text::CSV->new ( { binary => 1 } );

# Write a header for $fh_table
my @HEADER = (
    'scientificname', 'tax_class', 'tax_order'
);
foreach my $lang (@LANGUAGES) {
    push @HEADER, "$lang\_name";
    push @HEADER, "$lang\_source" if $FLAG_PRINT_SOURCE;
}
$csv->combine(@HEADER);
say $fh_table $csv->string;

# Write a header for $fh_missing
@HEADER = ( 'scientificname', 'tax_class', 'tax_order', 'lang', 'cmname', 'source');
$csv->combine(@HEADER);
say $fh_missing $csv->string;

# Count groups.
my %groups;

# For each name:
my $count_scname = 0;
foreach my $row (@$scientific_names) {
    my $scname = $row->[0];
    $count_scname++;

    # Extract all entries for this name for the languages we're interested in.
    $sth = $db->prepare("SELECT cmname, LOWER(lang) AS lang_lower, COUNT(*) AS count_cmname,"
        . " array_agg(source) AS sources, "
        . " array_agg(tax_kingdom),"
        . " array_agg(tax_phylum),"
        . " array_agg(tax_class),"
        . " array_agg(tax_order),"
        . " array_agg(tax_family),"
        . " array_agg(tax_genus)"
        . " FROM entries"
        . " WHERE scname=? AND LOWER(lang) IN ($languages) AND source NOT LIKE 'GBIF%'"
        . " GROUP BY cmname, lang_lower"
        . " ORDER BY count_cmname DESC, lang_lower DESC"
    );

    $sth->execute($scname);
    my $rows = $sth->fetchall_arrayref();
    my %names = ();
    my %sources = ();

    my %higher_taxonomy = ();
    
    # Summarize names for every language.
    foreach my $entry (@$rows) {
        my $cmname = $entry->[0];
        my $lang = $entry->[1];
        my $count = $entry->[2];
        my $source_list = $entry->[3];
        
        $higher_taxonomy{'kingdom'}{$_ // 'null'} = 1 foreach @{$entry->[4]};
        $higher_taxonomy{'phylum'}{$_ // 'null'} = 1 foreach @{$entry->[5]};
        $higher_taxonomy{'class'}{$_ // 'null'} = 1 foreach @{$entry->[6]};
        $higher_taxonomy{'order'}{$_ // 'null'} = 1 foreach @{$entry->[7]};
        $higher_taxonomy{'family'}{$_ // 'null'} = 1 foreach @{$entry->[8]};
        $higher_taxonomy{'genus'}{$_ // 'null'} = 1 foreach @{$entry->[9]};

        if(exists $names{$lang}) {
            if($FLAG_CONCAT_ALL) {
                # Concat the new name at the end of the previous name
                $names{$lang} .= "|$cmname";
                $sources{$lang}{$_} = 1 foreach @$source_list;
            } else {
                # Ignore all but the first name.
            }
        } else {
            # Add the first common name.
            $names{$lang} = $cmname;
            $sources{$lang}{$_} = 1 foreach @$source_list;
        }
    }

    my @results = ($scname);
    my $str_class = join('|', sort keys %{$higher_taxonomy{'class'}});
    push @results, $str_class;
    my $str_order = join('|', sort keys %{$higher_taxonomy{'order'}});
    push @results, $str_order;

    foreach my $lang (@LANGUAGES) {
        my $name_status;

        if(exists $names{$lang}) {
            push @results, $names{$lang};
            push @results, join('|', sort keys $sources{$lang})
                if $FLAG_PRINT_SOURCE;

            $name_status = 'filled';
        } else {
            push @results, undef;
            push @results, undef 
                if $FLAG_PRINT_SOURCE;

            $name_status = 'missing';

            $csv->combine(
                $scname,
                $str_class,
                $str_order,
                $lang,
                "",
                ""
            );
            say $fh_missing $csv->string;
        }

        # Add to groups.
        sub add_to_group($$$) {
            my ($group_name, $lang, $name_status) = @_;

            unless(exists $groups{$group_name}{$lang}{$name_status}) {
                $groups{$group_name}{$lang}{$name_status} = 0;
            }

            $groups{$group_name}{$lang}{$name_status}++;
        }

        add_to_group('all', $lang, $name_status);
        add_to_group("class $str_class", $lang, $name_status) 
            unless $str_class eq '';
        add_to_group("order $str_order", $lang, $name_status) 
            unless $str_order eq '';
    }
    $csv->combine(@results);
    say $fh_table $csv->string;
}

# Print the group results.
@HEADER = ('group');
foreach my $lang (@LANGUAGES) {
    push @HEADER, ("$lang\_filled", "$lang\_missing", "$lang\_perc_filled");
}
$csv->combine(@HEADER);
say $fh_summary $csv->string;

foreach my $group (sort keys %groups) {
    my @results = ($group);

    foreach my $lang (@LANGUAGES) {
        my $count_filled;
        my $count_missing;
        if(exists $groups{$group}{$lang}) {
            $count_filled = $groups{$group}{$lang}{'filled'} // 0;
            $count_missing = $groups{$group}{$lang}{'missing'} // 0;
        }

        if(0 == $count_filled + $count_missing) {
            push @results, (
                $count_filled,
                $count_missing,
                "NA"
            );
        } else {
            push @results, (
                $count_filled,
                $count_missing,
                sprintf("%.2f%%", (($count_filled)/($count_filled + $count_missing) * 100))
            );
        }
    }

    $csv->combine(@results);
    say $fh_summary $csv->string;
}

close($fh_table);
close($fh_summary);
close($fh_missing);
