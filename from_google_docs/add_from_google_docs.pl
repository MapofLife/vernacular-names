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
our $FLAG_DEBUG_ONLY = 1;

my $db = DBI->connect("dbi:Pg:dbname=common_names;host=127.0.0.1;port=5432", 
    'vaidyagi', '', 
{
    AutoCommit => 1,
    RaiseError => 1,
    PrintError => 1
});

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

# Make a directory to store the downloads.
rmtree("data/google_docs");
mkdir("data/google_docs");

foreach my $dataset_name (keys %google_datasets) {
    my $dataset_id = $google_datasets{$dataset_name};

    say STDERR "Processing $dataset_name ($dataset_id).";

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
    while(my $row = $csv->getline_hr($fh)) {
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

        # Look for identifiers.
        my $url = undef;

        if(exists $row->{'url'}) {
            $url = $row->{'url'};
            $column_names_used{'url'} = 1;
        }

        # Check for source_url and source_priority.
        my $source_url = undef;
        my $source_priority = undef;

        if(exists $row->{'source_url'}) {
            $source_url = $row->{'source_url'};
            $column_names_used{'source_url'} = 1;
        }

        if(exists $row->{'source_priority'}) {
            $source_priority = $row->{'source_priority'};
            $column_names_used{'source_priority'} = 1;
        }

        # See if we can also get higher taxonomy.
        my $tax_class = undef;
        my $tax_order = undef;
        my $tax_family = undef;

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

        # Ignore everything else.
        my @ignored_colnames = ();
        foreach my $colname ($csv->column_names) {
            unless(exists $column_names_used{$colname}) {
                push @ignored_colnames, $colname;
            }
        }

        say STDERR "    - The following columns were ignored: " . join(', ', @ignored_colnames)
            if $flag_first_row;

        # No longer the first row.
        $flag_first_row = 0;
    }
}

exit;

my $csv = "";
my $fh = "";

my $row_count = 0;
while(my $row = $csv->getline_hr($fh)) {
    $row_count++;

    # Fields we're interested in.
    my $canonicalName = $row->{'genus'};
    my $eol_best_match_id = $row->{'eol_best_match_id'};
    my $json = $row->{'eol_common_name_and_synonyms'};

    # Fix JSON.
    #$json =~ s/^\s*"//g;
    #$json =~ s/"\s*$//g;
    #$json =~ s/""/"/g;

    # Some standard ones.
    my $url = "http://eol.org/pages/$eol_best_match_id/names/common_names";
    my $source = "EOL API calls, July 12, 2014: genus names without english common names";
    my $source_url = "http://eol.org/api/";
    my $tax_order;
    my $tax_class;

    # Try to parse the JSON.
    if($json ne '') {
    try {
        my $eol_results = decode_json($json);

        # Look up higher taxonomy.
        my %higher_taxonomy;
        my $eol_higher = $row->{'eol_higher_taxonomy'};

        try {
            my $taxonHierarchy = decode_json($eol_higher);
            my $ancestors = $taxonHierarchy->{'ancestors'};

            foreach my $ancestor (@$ancestors) {
                if(exists $ancestor->{'taxonRank'} && exists $ancestor->{'scientificName'}) {
                    $higher_taxonomy{lc $ancestor->{'taxonRank'}} = lc $ancestor->{'scientificName'};
                } else {
                    warn "Could not parse ancestor while processing $canonicalName: " . Dumper($ancestor);
                }
            }

        } catch {
            warn "Could not parse taxon hierarchy: <<$eol_higher>>.";
        };

        # Display higher taxonomy if debugging.
        say STDERR "  Higher taxonomy: " . Dumper(\%higher_taxonomy)
            if $FLAG_DEBUG_ONLY;

        # Store some higher taxonomy.
        my $tax_kingdom = $higher_taxonomy{'kingdom'};
        my $tax_phylum = $higher_taxonomy{'phylum'};
        $tax_class = $higher_taxonomy{'class'};
        $tax_order = $higher_taxonomy{'order'};
        my $tax_family = $higher_taxonomy{'family'};
        my $tax_genus = $higher_taxonomy{'genus'};

        if($FLAG_DEBUG_ONLY) {
            say STDERR "\n\n$row_count.\n\nAdding la name: $canonicalName from $source_url";
        } else {
            $db->do("INSERT INTO entries " .
                "(scname, cmname, lang, source, url, source_url, tax_kingdom, tax_phylum, tax_class, tax_order, tax_family, tax_genus) " .
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                {}, 
                $canonicalName, $canonicalName, 'la', $source, $url, $source_url,
                $tax_kingdom, $tax_phylum, $tax_class, $tax_order, $tax_family, $tax_genus
            );
        }

        my $commonNames = $eol_results->{'vernacularNames'};

        foreach my $commonName (@$commonNames) {
            my $cmname = $commonName->{'vernacularName'};
            my $lang = $commonName->{'language'};

            # Now add them into the database.
            if($FLAG_DEBUG_ONLY) {
                say STDERR "  Adding $lang to $canonicalName: $cmname from $source_url";
            } else {
                $db->do("INSERT INTO entries " .
                    "(scname, cmname, lang, source, url, source_url, tax_kingdom, tax_phylum, tax_class, tax_order, tax_family, tax_genus) " .
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    {}, 
                    $canonicalName, $cmname, $lang, $source, $url, $source_url,
                    $tax_kingdom, $tax_phylum, $tax_class, $tax_order, $tax_family, $tax_genus
                );
            }
        }
    
    } catch {
        chomp;

        my $header = (split "\n", $json)[0];

        $tax_order ||= "<null>";
        $tax_class ||= "<null>";

        say STDERR "$canonicalName ($tax_order|$tax_class): $header\n\t<<$_>>";
    };
    }

}


close($fh);
