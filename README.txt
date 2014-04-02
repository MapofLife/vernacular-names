A vernacular names framework for Map of Life
============================================

We need one place to put all the common names in a way that is easy 
to manipulate. We’re going to put it all into a single table with 
the following structure.

For now, I’m using the language ‘la’ to store the definitive list of
species currently in Map of Life; I’ll delete it once I have a better
way of storing lists in this system.

To pull out binomial names, I used:
 - UPDATE entries SET binomial = split_part(scname, ' ', 1) || ' ' || split_part(scname, ' ', 2);

To blank family, order and class names which ought to be blank
 - UPDATE entries SET tax_class = NULL WHERE tax_class = '';
 - UPDATE entries SET tax_order = NULL WHERE tax_order = '';
 - UPDATE entries SET tax_family = NULL WHERE tax_family = '';