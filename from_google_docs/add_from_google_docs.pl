#!/usr/bin/perl -w

use v5.012;

use strict;
use warnings;

use Try::Tiny;
use Text::CSV;
use DBI;
use DBD::Pg;
use JSON;
use Data::Dumper;
use LWP::UserAgent;
use File::Path;

# If = 1: no writes to database, only write to STDOUT.
our $FLAG_DEBUG_ONLY = 0;

my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432", 
    'vaidyagi', '', 
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

# The time at which this script was run.
my $script_date = localtime;

# Load the configuration file.
my %google_datasets;
open(my $fh_config, "<:encoding(utf8)", "data/google_docs.txt")
    or die "Could not open 'data/google_docs.txt': $!";
while(<$fh_config>) {
    chomp;

    if(/^\s*(.*)\s*:\s*(.*?)\s*$/) {
        die "Duplicate Google document: $1." if(exists $google_datasets{$1});
        $google_datasets{$1} = $2;
    } else {
        die "Could not read line from configuration file: $_";
    }
}

close($fh_config);

# Set up browser.
my $ua = LWP::UserAgent->new;

# Delete all previous entries.
my $SOURCE_IDENTIFIER = "$0";
if($FLAG_DEBUG_ONLY) {
    say " - Would delete previous records for '%$SOURCE_IDENTIFIER' if not in debug mode.";
} else {
    try {
        my $rows = $db->do("DELETE FROM entries WHERE source LIKE ?", {}, "\%$SOURCE_IDENTIFIER");
        
        die $db->err unless $rows;

        say " - Deleted $rows rows containing previous records for '%$SOURCE_IDENTIFIER'.";
    } catch {
        die "ERROR: Unable to delete previous records for '%$SOURCE_IDENTIFIER': $_";
    };
}

# Make a directory to store the downloads.
rmtree("data/google_docs");
mkdir("data/google_docs");

# Count.
my $count_entries = 0;

# Process each dataset.
foreach my $dataset_name (keys %google_datasets) {
    my $dataset_id = $google_datasets{$dataset_name};
    my $dataset_download_date = $script_date;

    say STDERR " - Processing $dataset_name ($dataset_id).";

    # Download file.
    my $response = $ua->get("https://docs.google.com/spreadsheets/d/$dataset_id/export?format=csv");
    unless($response->is_success) {
        warn "Could not download dataset '$dataset_name', skipping: <<" . Dumper($response) . ">>";
        next;
    }

    # Download CSV.
    my $filename = "data/google_docs/$dataset_id.csv";
    say STDERR " - Downloading Google Doc.";
    open(my $fh_csv, ">:encoding(utf8)", $filename)
        or die "Could not create '$filename': $!";

    print $fh_csv $response->decoded_content;

    close($fh_csv);
    say STDERR " - Google Doc downloaded.";

    # Read Google Doc CSV.
    say STDERR " - Reading CSV.";

    my $csv = Text::CSV->new({ 
        binary => 1,
    #    quote_char => undef,
    #    escape_char => undef,
    #    sep_char => "\t",
        eol => $/
    })
        or die "CSV failed";

    open(my $fh, "<:encoding(utf8)", $filename)
        or die "Could not open CSV file '$filename': $!";
    $csv->column_names($csv->getline($fh));

    say STDERR "    - Column names: " . join(", ", $csv->column_names);

    my $flag_first_row = 1;
    my $row_count = 0;
    while(my $row = $csv->getline_hr($fh)) {
        $row_count++;

        # At a minimum, we need scientificName, lang, vernacularName, source
        my %column_names_used;
        foreach my $colname ('scientificName', 'lang', 'vernacularName', 'source') {
            die "Google Doc dataset $dataset_name does not contain required column name $colname"
                unless exists $row->{$colname};
            $column_names_used{$colname} = 1;
        }

        my $scname = $row->{'scientificName'};
        my $cmname = $row->{'vernacularName'};
        my $lang = $row->{'lang'};
        my $source = $row->{'source'};

        if($scname eq "") {
            say STDERR "   - Skipping row $row_count: no scientific name provided.";
            next;
        }

        if(($cmname eq "") or ($lang eq "") or ($source eq "")) {
            my @fieldnames;

            push @fieldnames, "vernacularName" if $cmname eq "";
            push @fieldnames, "lang" if $lang eq "";
            push @fieldnames, "source" if $source eq "";

            say STDERR "   - Skipping row $scname: required field(s) " . join(", ", @fieldnames) . " missing.";
            next;
        }

        # The 'add_from_google_docs.pl' is how we find records we added, so
        # make sure that stays in!
        $source .= ", $dataset_name from $dataset_id downloaded on $dataset_download_date using $SOURCE_IDENTIFIER";

        # Look for identifiers.
        my $url = undef;

        if(exists $row->{'url'}) {
            $url = $row->{'url'};
            $column_names_used{'url'} = 1;
        }

        # Check for source_url and source_priority.
        my $source_url = undef;
        my $source_priority = 0;

        if(exists $row->{'source_url'}) {
            $source_url = $row->{'source_url'};
            $column_names_used{'source_url'} = 1;
        }

        if(exists $row->{'source_priority'}) {
            $source_priority = $row->{'source_priority'};
            $column_names_used{'source_priority'} = 1;
        }

        # See if we can also get higher taxonomy.
        my $tax_kingdom = undef;
        my $tax_phylum = undef;
        my $tax_class = undef;
        my $tax_order = undef;
        my $tax_family = undef;
        my $tax_genus = undef;

        if(exists $row->{'class'}) {
            $tax_class = $row->{'class'};
            $column_names_used{'class'} = 1;
        }

        if(exists $row->{'order'}) {
            $tax_order = $row->{'order'};
            $column_names_used{'order'} = 1;
        }

        if(exists $row->{'family'}) {
            $tax_family = $row->{'family'};
            $column_names_used{'family'} = 1;
        }

        # Two other fields we calculate later on.
        my $binomial = undef;
        my $genus = undef;

        # Ignore everything else.
        my @ignored_colnames = ();
        foreach my $colname ($csv->column_names) {
            unless(exists $column_names_used{$colname}) {
                push @ignored_colnames, $colname;
            }
        }

        say STDERR "    - The following columns were ignored: " . join(', ', @ignored_colnames)
            if $flag_first_row;

        # Write the values into the database.
        $count_entries++;
        if($FLAG_DEBUG_ONLY) {
            say "   - Adding vernacular name $cmname ($lang) to $scname ('$source' at priority $source_priority)";

            say "\t - URL: $url" if defined $url;

            say "\t - Source URL: $source_url" if defined $source_url;

            say "\t - Class: $tax_class." if defined $tax_class;
            say "\t - Order: $tax_order." if defined $tax_order;
            say "\t - Family: $tax_family." if defined $tax_family;

        } else {
            try {
                $db->do("INSERT INTO entries " .
                    "(scname, cmname, lang, source, url, source_url, tax_kingdom, tax_phylum, tax_class, tax_order, tax_family, tax_genus, source_priority, genus) " .
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    {},
                    $scname, $cmname, $lang, $source, $url, $source_url, $tax_kingdom,
                    $tax_phylum, $tax_class, $tax_order, $tax_family, $tax_genus,
                    $source_priority, $genus
                );

            } catch {
                warn "ERROR: could not insert record for $cmname ($lang) to $scname ('$source' at priority $source_priority), skipping dataset.";

                # We don't delete previous records, but rerunning this script
                # should sort all that out.

                next;
            };
        }

        # No longer the first row.
        $flag_first_row = 0;
    }
}

# Once we're done, try to set up all binomial names and genus names.
try {
    # I don't know what 'binomial' does, but emit_mol_table.pl uses it, and I'm
    # scared.
    $db->do("UPDATE entries SET binomial = split_part(scname, ' ', 1) || ' ' || split_part(scname, ' ', 2)" .
        "WHERE binomial = '0' AND split_part(scname, ' ', 2) != '' AND source LIKE ?;",
        {},
        "%$SOURCE_IDENTIFIER"
    );

    $db->do("UPDATE entries SET genus = split_part(scname, ' ', 1) " .
        "WHERE genus IS NULL AND split_part(scname, ' ', 2) != '' AND source LIKE ?;",
        {},
        "%$SOURCE_IDENTIFIER"
    );
} catch {
    die "ERROR: could not finish binomial and genus updates, reason: $_";
};

say STDERR "Completed, $count_entries entries added.";
