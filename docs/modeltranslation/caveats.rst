.. _caveats:

Caveats
=======

Accessing Translated Fields Outside Views
-----------------------------------------

Since the modeltranslation mechanism relies on the current language as it
is returned by the ``get_language`` function care must be taken when accessing
translated fields outside a view function.

Within a view function the language is set by Django based on a flexible model
described at `How Django discovers language preference`_ which is normally used
only by Django's static translation system.

.. _How Django discovers language preference: https://docs.djangoproject.com/en/dev/topics/i18n/translation/#how-django-discovers-language-preference

When a translated field is accessed in a view function or in a template, it
uses the ``django.utils.translation.get_language`` function to determine the
current language and return the appropriate value.

Outside a view (or a template), i.e. in normal Python code, a call to the
``get_language`` function still returns a value, but it might not what you
expect. Since no request is involved, Django's machinery for discovering the
user's preferred language is not activated. For this reason modeltranslation
adds a thin wrapper around the function which guarantees that the returned
language is listed in the ``LANGUAGES`` setting.

The unittests use the ``django.utils.translation.trans_real`` functions to
activate and deactive a specific language outside a view function.


South and Third-party Apps
--------------------------

Instead of using ``sync_translation_fields`` you may automatically generate a
South migration with ``schemamigration --auto``. This can be used to add and
remove translation columns from the database as necessary.

However, in case of a third-party app distributing its own South migration with
changes to translated fields, deciding how to reflect those changes for
translation fields may need careful inspection.

If you'd rather be on the safe side and manage all database changes needed for
translations manually (while still using South for other migrations), you may
set ``MODELTRANSLATION_SOUTH_IGNORE`` to hide all translation fields from the
South's automatic schema changes detection.
