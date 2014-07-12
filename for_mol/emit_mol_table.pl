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
our $WARN_IF_CMNAME_LONGER_THAN = 50;
our $FILE_MASTER_LIST = "./data/combined-2014jul11.txt";
our $FLAG_CONCAT_ALL = 0;
    # 0 = pick the most popular name for each language
    # 1 = concatenate all the names of each language
our $FLAG_PRINT_SOURCE = 1;
    # 0 = don't print sources
    # 1 = print sources
our $FLAG_PRINT_HIGHER = 1;
    # 0 = don't print higher taxonomy
    # 1 = print higher taxonomy
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

# Set up results folder.
mkdir("results");
our $OUTPUT_DIR = "results/" . time;
die "Results already exist!" if -e $OUTPUT_DIR;
mkdir($OUTPUT_DIR);

open(my $fh_table, ">:utf8", "$OUTPUT_DIR/mol_table.csv")
    or die "Could not create '$OUTPUT_DIR/mol_table.csv': $!";

open(my $fh_summary, ">:utf8", "$OUTPUT_DIR/mol_summary.csv")
    or die "Could not create '$OUTPUT_DIR/mol_summary.csv': $!";

open(my $fh_genera, ">:utf8", "$OUTPUT_DIR/mol_genera.csv")
    or die "Could not create '$OUTPUT_DIR/mol_genera.csv': $!";

open(my $fh_missing, ">:utf8", "$OUTPUT_DIR/mol_missing.csv")
    or die "Could not create '$OUTPUT_DIR/mol_missing.csv': $!";

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

# Retrieve a list of all species that need writing out.
my %distinct_scnames;
my %scname_source;
open(my $fh_master, "<:utf8", $FILE_MASTER_LIST) or die "Could not open '$FILE_MASTER_LIST': $!";
$csv->column_names($csv->getline($fh_master));
while(my $row = $csv->getline_hr($fh_master)) {
    my $scname = $row->{'binomial'};

    $distinct_scnames{$scname} = 1;
    
    if(exists $scname_source{lc $scname}) {
        $scname_source{lc $scname} .= ", " . $row->{'source'};
    } else {
        $scname_source{lc $scname} = $row->{'source'};
    }
}
close($fh_master);

my @scientific_names = keys %distinct_scnames;

$csv = Text::CSV->new ( { binary => 1 } );
my $sth;

# Write a header for $fh_table
my @HEADER = (
    'scientificname', 'mol_source', 'tax_class', 'tax_order', 'tax_family'
);
foreach my $lang (@LANGUAGES) {
    push @HEADER, "$lang\_name";
    push @HEADER, "$lang\_source" if $FLAG_PRINT_SOURCE;
    if($FLAG_PRINT_HIGHER) {
        push @HEADER, "$lang\_class";
        push @HEADER, "$lang\_class_source" if $FLAG_PRINT_SOURCE;
        push @HEADER, "$lang\_order";
        push @HEADER, "$lang\_order_source" if $FLAG_PRINT_SOURCE;
        push @HEADER, "$lang\_family";
        push @HEADER, "$lang\_family_source" if $FLAG_PRINT_SOURCE;
        push @HEADER, "$lang\_genus";
        push @HEADER, "$lang\_genus_source" if $FLAG_PRINT_SOURCE;
    }
}
$csv->combine(@HEADER);
say $fh_table $csv->string;

# Write a header for $fh_missing
@HEADER = ( 'scientificname', 'mol_source', 'tax_class', 'tax_order', 'tax_family', 'lang', 'cmname', 'source');
$csv->combine(@HEADER);
say $fh_missing $csv->string;

# Count groups.
my %groups;

# Add to groups.
sub add_to_group($$$) {
    my ($group_name, $lang, $name_status) = @_;

    unless(exists $groups{$group_name}{$lang}{$name_status}) {
        $groups{$group_name}{$lang}{$name_status} = 0;
    }

    $groups{$group_name}{$lang}{$name_status}++;
}
    
# Set up languages
my $languages = join(', ', map { "'$_'" } @LANGUAGES); 

# For each name:
my $count_scname = 0;
foreach my $scname (@scientific_names) {
    $count_scname++;

    # Extract all entries for this name for the languages we're interested in.
    $sth = $db->prepare("SELECT cmname, LOWER(lang) AS lang_lower, COUNT(*) AS count_cmname,"
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

    $sth->execute(lc $scname);
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
       
        $higher_taxonomy{'kingdom'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[4]};
        $higher_taxonomy{'phylum'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[5]};
        $higher_taxonomy{'class'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[6]};
        $higher_taxonomy{'order'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[7]};
        $higher_taxonomy{'family'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[8]};
        $higher_taxonomy{'tax_genus'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[9]};
        $higher_taxonomy{'genus'}{lc $_} = 1 foreach grep {defined($_)} @{$entry->[10]};

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
        
        warn "Common name \"$cmname\" for species $scname is longer than $WARN_IF_CMNAME_LONGER_THAN characters"
            if length($cmname) > $WARN_IF_CMNAME_LONGER_THAN;
 
    }

    my @results = ($scname, $scname_source{lc $scname});
    my $str_class = join('|', sort keys %{$higher_taxonomy{'class'}});
    push @results, $str_class;
    my $str_order = join('|', sort keys %{$higher_taxonomy{'order'}});
    push @results, $str_order;
    my $str_family = join('|', sort keys %{$higher_taxonomy{'family'}});
    push @results, $str_family;

    foreach my $lang (@LANGUAGES) {
        my $name_status;

        if(exists $names{$lang}) {
            push @results, ucfirst $names{$lang};
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
                $scname_source{lc $scname},
                $str_class,
                $str_order,
                $str_family,
                $lang,
                "",
                ""
            );
            say $fh_missing $csv->string;
        }

        # Add higher taxonomy
        my $flag_has_genus = 0;
        if($FLAG_PRINT_HIGHER) {
            # Remember, we need to concat onto @results: order, class, family

            $sth = $db->prepare("SELECT cmname, array_agg(source), COUNT(*) AS count_cmname"
                . " FROM entries"
                . " WHERE LOWER(scname)=? AND LOWER(lang)=?"
                . " GROUP BY cmname"
                . " ORDER BY count_cmname DESC"
                . " LIMIT 1"
            );

            foreach my $rank ("class", "order", "family", 'genus') {
                my @names = sort keys %{$higher_taxonomy{$rank}};
                my @higher_tax_names = ();
                my %higher_tax_sources = ();

                foreach my $name (@names) {
                    my $name = lc($name);
                    next if ($name eq '') or ($name eq 'null');

                    # say "Looking up higher taxonomy: '$name' in $lang.";
                    $sth->execute($name, $lang);
                    my $rows = $sth->fetchall_arrayref();

                    unless(exists $rows->[0]) {
                        # say "\tNot found.";
                    } else {
                        $flag_has_genus = 1 if $rank eq 'genus';

                        my $row = $rows->[0];
                        push @higher_tax_names, ucfirst $row->[0];
                        $higher_tax_sources{$_} = 1 foreach @{$row->[1]};
                        # say "\tNames: " . $row->[0];
                        # say "\tSources: " . join(', ', @{$row->[1]});  

                        warn "Common name \"$row->[0]\" for higher taxonomy $name is longer than $WARN_IF_CMNAME_LONGER_THAN characters"
                            if length($row->[0]) > $WARN_IF_CMNAME_LONGER_THAN;

                    } 

                }

                my $higher_name = join('|', @names);
                push @results, join("|", @higher_tax_names);
                push @results, join("|", sort keys %higher_tax_sources);

                if(0 == scalar @higher_tax_names) {
                    add_to_group("taxon $higher_name as $rank", $lang, 'missing');
                } else {
                    add_to_group("taxon $higher_name as $rank", $lang, 'filled');
                }
            } 
        }

        my $source_name = $scname_source{lc $scname};
        add_to_group("Source $source_name", $lang, $name_status);

        add_to_group('all', $lang, $name_status);
        add_to_group("class $str_class", $lang, $name_status) 
            unless $str_class eq '';
        add_to_group("order $str_order", $lang, $name_status) 
            unless $str_order eq '';

        add_to_group("all at genus level", $lang, $flag_has_genus ? 'filled' : 'missing');
        add_to_group("Source $source_name at genus level", $lang, $flag_has_genus ? 'filled' : 'missing');
        add_to_group("class $str_class at genus level", $lang, $flag_has_genus ? 'filled' : 'missing')
            unless $str_class eq '';
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
say $fh_genera $csv->string;

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
    if($group =~ /as genus$/) {
        say $fh_genera $csv->string;
    } else {
        say $fh_summary $csv->string;
    }
}

close($fh_table);
close($fh_summary);
close($fh_genera);
close($fh_missing);

# Report time.
my $end_time = time;

say "Completed in " . ($end_time - $start_time) . " seconds.";
