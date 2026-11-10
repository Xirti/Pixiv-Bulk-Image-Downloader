"""Public release trust anchors. NEVER add private keys to this file.

Rotation: ship an overlap release signed by the old key before switching.
"""

TRUSTED_RELEASE_KEYS: dict[str, str] = {'e2e18b2c9da4a9be1c8fa5e0297a3367488c874185e11f14833eb4abc80b3711': '7b30844229546104fcd4ea80c60c45ddfaa3278744ab725cd59318d87a45038f'}
SIGNATURE_THRESHOLD = 1
