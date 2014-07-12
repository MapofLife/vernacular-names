A vernacular names framework for Map of Life
============================================

We need one place to put all the common names in a way that is easy 
to manipulate. We’re going to put it all into a single table with 
the following structure.

Languages: en, es, pt, de, fr, zh

For now, I’m using the language ‘la’ to store the definitive list of
species currently in Map of Life; I’ll delete it once I have a better
way of storing lists in this system.

To pull out binomial names, I used:
 - UPDATE entries SET binomial = split_part(scname, ' ', 1) || ' ' || split_part(scname, ' ', 2);

To blank family, order and class names which ought to be blank
 - UPDATE entries SET tax_class = NULL WHERE tax_class = '';
 - UPDATE entries SET tax_order = NULL WHERE tax_order = '';
 - UPDATE entries SET tax_family = NULL WHERE tax_family = '';

GENUS: (only if binomial!)
 - Update using:
	UPDATE entries SET genus = split_part(scname, ' ', 1) WHERE 
		genus IS NULL 
		AND split_part(scname, ' ', 2) != '';

To fill in genera, you can use the following SQL, but it's probably a bad idea, as it means tax_genus comes from scname NOT from the source:
 - UPDATE entries SET tax_genus = split_part(binomial, ' ', 1) WHERE binomial != 0::text AND tax_genus IS NULL;

If you want to do that, there are exactly 41 rows you need to fix:
 - SELECT DISTINCT tax_genus, split_part(binomial, ' ', 1) AS tax_genus_implied FROM entries WHERE tax_genus IS NOT NULL AND lower(tax_genus) != lower(split_part(binomial, ' ', 1));

CUSTOM OPERATIONS:
 - MOL taxonomy table: I deleted entries with multiple comma-separated names using this:
	- UPDATE entries SET cmname = trim(both from split_part(cmname, ',', 1)) WHERE cmname LIKE '%,%' AND source = 'Map of Life ''taxonomy'' table as of February 28, 2014'