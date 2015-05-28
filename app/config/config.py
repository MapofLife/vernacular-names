# vim: set fileencoding=utf-8 :

# config.py: Configuration settings for NomDB.
# These are the defaults, but you can adjust

__author__ = 'Gaurav Vaidya'

# Configuration. vnapi constants are used for configuration in other code.
DEADLINE_FETCH = 60 # seconds to wait during URL fetch (max: 60)

# Min, max and default values for source_priority
PRIORITY_MIN = 0
PRIORITY_MAX = 100
PRIORITY_DEFAULT = 0
PRIORITY_DEFAULT_APP = 80