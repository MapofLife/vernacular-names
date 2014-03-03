CREATE TABLE IF NOT EXISTS entries (
	id SERIAL PRIMARY KEY,		-- primary key 
	scname TEXT NOT NULL,		-- scientific name 
	cmname TEXT NOT NULL,		-- common name
	lang TEXT NOT NULL,		-- IETF language tag (e.g. “en”, “en-uk”, “nan-Hant-TW”)
	source TEXT NOT NULL,		-- A short-title description of the source, e.g. “GBIF Nub 2013-01-12”, “Catalogue of Life Annual Checklist 2012”, etc.
	url TEXT,			-- A URL for this entry
	source_url TEXT,		-- A URL for the source
        tax_kingdom TEXT,               -- Higher taxonomy: kingdom
        tax_phylum TEXT,                -- Higher taxonomy: phylum
        tax_class TEXT,                 -- Higher taxonomy: class
        tax_order TEXT,                 -- Higher taxonomy: order
        tax_family TEXT,                -- Higher taxonomy: family
        tax_genus TEXT,                 -- Higher taxonomy: genus
	added DATE DEFAULT current_date	-- The date this name was added to the pool.
);

-- So we can look up entries by scientific name. 
CREATE INDEX entries_scname ON entries (scname);

Or case insensitive
CREATE INDEX entries_scname_lc ON entries (LOWER(scname))

-- Might be useful to group this way around too.
CREATE INDEX entries_cmname ON entries (cmname);

-- To pull a source back out.
CREATE INDEX entries_source ON entries (source);
