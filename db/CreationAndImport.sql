-- DROP TABLE vernacular_names;
CREATE TABLE IF NOT EXISTS vernacular_names (
	id INTEGER,		        -- primary key (NO LONGER USED)
	scname TEXT,			-- scientific name (canonical name)
	binomial TEXT, 			-- Binomial name (scname with genus and species)
	genus TEXT,			-- If a binomial name, set 'genus' here.
					-- tax_genus is from higher taxonomy information,
					-- while genus is ONLY derived from scname.
	cmname TEXT,			-- common name
	lang TEXT,			-- IETF language tag (e.g. “en”, “en-uk”, “nan-Hant-TW”)
	source TEXT,			-- A short-title description of the source, e.g. “GBIF Nub 2013-01-12”, “Catalogue of Life Annual Checklist 2012”, etc.
	url TEXT,			-- A URL for this entry
	source_url TEXT,		-- A URL for the source
	source_priority INTEGER, 	-- The priority of this source: eventually,
					-- we’ll sort results by this value.
        tax_kingdom TEXT,               -- Higher taxonomy: kingdom
        tax_phylum TEXT,                -- Higher taxonomy: phylum
        tax_class TEXT,                 -- Higher taxonomy: class
        tax_order TEXT,                 -- Higher taxonomy: order
        tax_family TEXT,                -- Higher taxonomy: family
        tax_genus TEXT,                 -- Higher taxonomy: genus
        added_by TEXT,                  -- User who added this entry.
	created_at TIMESTAMP,           -- The date this name was added to CartoDB.
        updated_at TIMESTAMP,		-- The date this name was last updated in CartoDB.
	added TEXT, 			-- The date on which this name was added (NO LONGER USED)
	the_geom TEXT,			-- CartoDB geometry information (NOT USED)
	cartodb_id INTEGER PRIMARY KEY	-- CartoDB primary key
);

-- How to import a CartoDB-exported CSV into a local PostgreSQL database.
-- Copy data into vernacular_names.
COPY vernacular_names (id,scname,cmname,lang,source,url,source_url,tax_kingdom,tax_phylum,tax_class,tax_order,tax_family,tax_genus,added,source_priority,binomial,genus,the_geom,cartodb_id,created_at,updated_at,added_by)
	FROM 'vernacular_names as of May 24, 2015.csv'
	(FORMAT CSV, HEADER TRUE);

-- Indexes

-- So we can look up entries by scientific name. 
CREATE INDEX vernacular_names_scname ON vernacular_names (scname);

-- Or case insensitive
CREATE INDEX vernacular_names_scname_lc ON vernacular_names (LOWER(scname));

-- Ditto on binomial names because LIFE.
CREATE INDEX vernacular_names_binomial ON vernacular_names (binomial);
CREATE INDEX vernacular_names_binomial_lc ON vernacular_names (LOWER(binomial));

-- Might be useful to group this way around too.
CREATE INDEX vernacular_names_cmname ON vernacular_names (cmname);

-- To pull a source back out.
CREATE INDEX vernacular_names_source ON vernacular_names (source);

-- Add the master list.
-- DROP TABLE species_for_vernacular_names
CREATE TABLE IF NOT EXISTS species_for_vernacular_names (
    dataset TEXT,
    scientificname TEXT,
    genus TEXT,
    family TEXT,
    family_source TEXT,
    iucn_red_list_status TEXT,
    the_geom TEXT,              -- NOT USED
    cartodb_id INTEGER PRIMARY KEY,
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);

-- Copy data into species_for_vernacular_names
COPY species_for_vernacular_names (dataset,scientificname,genus,family,family_source,iucn_red_list_status,the_geom,cartodb_id,created_at,updated_at)
	FROM 'species_for_vernacular_names as of May 24, 2015.csv'
	(FORMAT CSV, HEADER TRUE);


