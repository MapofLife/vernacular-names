Database schema
===============

The `access.*` names refer to https://github.com/MapofLife/vernacular-names/blob/develop/app/config/access.py.skeleton, which you should configure for your individual setup.

# All names list table (`access.ALL_NAMES_TABLE`)

The list of every vernacular name we know about.

 * `scname` (required): the scientific name at any taxonomic rank, in lowercase (e.g. `panthera`, `panthera tigris`)
 * `cmname` (required): the vernacular (common) name (e.g. `tiger`, `बाघ`)
   * Titlecased using https://pypi.python.org/pypi/titlecase before export/use.
 * `lang` (required): the language code (e.g. `en`, `pt-BR`, `zh-Hans`)
 * `url`: a URL that uniquely identifies the vernacular name; examples:
   * https://hi.wikipedia.org/wiki/%E0%A4%AC%E0%A4%BE%E0%A4%98
   * http://eol.org/pages/328674/names
 * `source` (required): the name of the source (e.g. `EOL`, `English Wikipedia`)
 * `source_url`: The URL of the source (e.g. http://en.wikipedia.org/)
 * `source_priority` (required, integer between 0 and 100): the priority of this source.
   * NomDB sets the default priority on manual changes to 80. Therefore, source priorities above 80 can't be overriden in NomDB.
   * Bulk uploads use a default priority of 0, but can be set as high as possible.
   * This field is deliberately kept denormalized, so that source priorities can be tweaked as necessary. The sources page on NomDB lets you see what source priorities have been set for each source.
 * `added_by`: The username of the user that added this name.
   * At the moment, this is the e-mail address from Google's authentication. Eventually, we'll replace this with a userid from MOL's authentication system.
 * `created_at`: The datetime at which this record was added to the database.
 * `updated_at`: The datetime at which this record was last updated to the database.

To pick the best common name for a given scientific name and language code, we sort by descending <tt>source_priority</tt>. If two common names have the same <tt>source_priority</tt>, the most recently created name (according to <tt>created_at</tt>) is used.

The following fields store higher taxonomy information by source. This allows NomDB to store taxonomies for any given scientific name by source. The MOL taxonomy system is a much better solution to this problem, so these fields are deprecated and will eventually be deleted.

 * `tax_kingdom`
 * `tax_phylum`
 * `tax_class`
 * `tax_order`
 * `tax_family`
 * `tax_genus`

The following fields are no longer used, are deprecated, and will eventually be deleted.

 * `genus`: The genus name that goes with this scientific name.
 * `binomial`: The binomial name that goes with this scientific name.
 * `added`: The date on which this common name was added to the database. We now use `updated_at` instead.

# Master list (`access.MASTER_LIST`)

The master list of scientific names organized by dataset. This allows us to generate lists of names by dataset. Other parts of MOL are better at dealing with this, so this will eventually be replaced, but for now this is relatively easy to update and test, and so we leave it in.

 * `scientificname` (required): The scientific name.
 * `dataset` (required): The dataset this scientific name belongs in. 
 * `family` (required): The family this scientific name belongs to.
 * `family_source`: The source that asserts that `scientificname` belongs in `family`.
 * `genus`: The genus name of this species. **Deprecated, will be deleted.**
 * `iucn_red_list_status`: The IUCN Red List status of this species.
