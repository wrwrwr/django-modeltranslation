# -*- coding: utf-8 -*-
from django.conf import settings
from django.db.models import Manager
from django.db.models.base import ModelBase

from modeltranslation.fields import (TranslationFieldDescriptor,
                                     create_translation_field)
from modeltranslation.manager import MultilingualManager, rewrite_lookup_key
from modeltranslation.utils import build_localized_fieldname


class AlreadyRegistered(Exception):
    pass


class NotRegistered(Exception):
    pass


class FieldsAggregationMetaClass(type):
    """
    Meta class to handle custom inheritance of fields between classes.
    """
    def __new__(cls, name, bases, attrs):
        attrs['fields'] = set(attrs.get('fields', ()))
        for base in bases:
            if isinstance(base, FieldsAggregationMetaClass):
                attrs['fields'].update(base.fields)
        attrs['fields'] = tuple(attrs['fields'])
        return super(FieldsAggregationMetaClass, cls).__new__(cls, name,
                                                              bases, attrs)


class TranslationOptions(object):
    """
    Translatable fields are declared by registering a model using
    ``TranslationOptions`` class with appropriate ``fields`` attribute.
    Model-specific fallback values adn languages can also be given as class
    attributes.

    Options' instances hold info about fields translated for a model and all
    its superclasses.
    The ``fields`` attribute lists fields that are handled in the model's
    database table (those inherited from abstract superclasses, unless there
    is a concrete superclass in between in the inheritance chain);
    ``localized_fieldnames`` holds a mapping from original field names to
    their translation clones. In some cases we'd like to know all translation
    fields available for a model, including those inherited from concrete
    supermodels (e.g. admin forms) -- attributes prefixed with ``inherited``
    include those.
    """
    __metaclass__ = FieldsAggregationMetaClass

    def __init__(self, model):
        self.model = model
        self.registered = False
        # Models registered with the same TranslationOptions class may inherit
        # different fields, so create a copy of ``fields`` on instance.
        self.fields = set(self.fields)
        self.inherited_fields = set(self.fields)
        self.localized_fieldnames = {}
        self.inherited_localized_fieldnames = {}

    def update(self, other):
        if other.model._meta.abstract:
            # ``fields`` and ``localized_fields`` don't contain fields
            # from conrete supermodels.
            self.fields.update(other.fields)
            self.localized_fieldnames.update(other.localized_fieldnames)
        self.inherited_fields.update(other.inherited_fields)
        self.inherited_localized_fieldnames.update(
            other.inherited_localized_fieldnames)

    def __str__(self):
        return '%s: %s + %s' % (self.__class__.__name__, tuple(self.fields),
                                tuple(self.inherited_fields - self.fields))


def add_localized_fields(model, fields):
    """
    Monkey patches the original model class to provide additional fields for
    every language.

    Returns a dict mapping the original fieldname to a list containing the
    names of the localized fields created for the original field.
    """
    localized_fields = dict()
    for field_name in fields:
        localized_fields[field_name] = list()
        for l in settings.LANGUAGES:
            # Create a dynamic translation field
            translation_field = create_translation_field(
                model=model, field_name=field_name, lang=l[0])
            # Construct the name for the localized field
            localized_field_name = build_localized_fieldname(field_name, l[0])
            # Check if the model already has a field by that name
            if hasattr(model, localized_field_name):
                raise ValueError(
                    "Error adding translation field. Model '%s' already "
                    "contains a field named '%s'." % (
                        model._meta.object_name, localized_field_name))
            # This approach implements the translation fields as full valid
            # django model fields and therefore adds them via add_to_class
            model.add_to_class(localized_field_name, translation_field)
            localized_fields[field_name].append(localized_field_name)
    return localized_fields


def add_manager(model):
    """
    Monkey patches the original model to use MultilingualManager instead of
    default manager (``objects``).

    If model has a custom manager, then merge it with MultilingualManager.
    """
    if not hasattr(model, 'objects'):
        return
    current_manager = model.objects
    if isinstance(current_manager, MultilingualManager):
        return
    if current_manager.__class__ is Manager:
        current_manager.__class__ = MultilingualManager
    else:
        class NewMultilingualManager(MultilingualManager, current_manager.__class__):
            pass
        current_manager.__class__ = NewMultilingualManager


def patch_constructor(model):
    """
    Monkey patches the original model to rewrite fields names in __init__
    """
    old_init = model.__init__

    def new_init(self, *args, **kwargs):
        for key, val in kwargs.items():
            new_key = rewrite_lookup_key(model, key)
            # Old key is intentionally left in case old_init wants to play with it
            kwargs.setdefault(new_key, val)
        old_init(self, *args, **kwargs)
    model.__init__ = new_init


#def translated_model_initialized(field_names, instance, **kwargs):
    #print "translated_model_initialized instance:", \
          #instance, ", field:", field_names
    #for field_name in field_names:
        #initial_val = getattr(instance, field_name)
        #print "  field: %s, initialval: %s" % (field_name, initial_val)
        #setattr(instance.__class__, field_name,
                #TranslationFieldDescriptor(field_name, initial_val))


#def translated_model_initializing(sender, args, kwargs, **signal_kwargs):
    #print "translated_model_initializing", sender, args, kwargs
    #trans_opts = translator.get_options_for_model(sender)
    #for field_name in trans_opts.fields:
        #setattr(sender, field_name, TranslationFieldDescriptor(field_name))


def delete_cache_fields(model):
    opts = model._meta
    try:
        del opts._field_cache
    except AttributeError:
        pass
    try:
        del opts._field_name_cache
    except AttributeError:
        pass
    try:
        del opts._name_map
    except AttributeError:
        pass


class Translator(object):
    """
    A Translator object encapsulates an instance of a translator. Models are
    registered with the Translator using the register() method.
    """
    def __init__(self):
        # model_class class -> ``TranslationOptions`` instance
        self._registry = {}

    def register(self, model_or_iterable, opts_class=None, **options):
        """
        Registers the given model(s) with the given translation options.

        The model(s) should be Model classes, not instances.

        If a model is already registered for translation, this will raise
        AlreadyRegistered.
        """
        if isinstance(model_or_iterable, ModelBase):
            model_or_iterable = [model_or_iterable]

        for model in model_or_iterable:
            # Find inherited fields and create options instance for the model.
            opts = self._get_options_for_model(model, opts_class, **options)

            # Mark the object explicitly as registered -- registry caches
            # options of all models, registered or not.
            if opts.registered:
                raise AlreadyRegistered('The model %s is already registered '
                                        'for translation' % model.__name__)
            opts.registered = True

            # Add the localized fields to the model.
            localized_fieldnames = add_localized_fields(model, opts.fields)
            opts.localized_fieldnames.update(localized_fieldnames)
            opts.inherited_localized_fieldnames.update(localized_fieldnames)

            # Delete all fields cache for related model (parent and children)
            for related_obj in model._meta.get_all_related_objects():
                delete_cache_fields(related_obj.model)

            # Set MultilingualManager
            add_manager(model)

            # Patch __init__ to rewrite fields
            patch_constructor(model)

            # Substitute original field with descriptor
            model_fallback_values = getattr(
                opts, 'fallback_values', None)
            model_fallback_languages = getattr(
                opts, 'fallback_languages', None)
            for field_name in opts.fields:
                if model_fallback_values is None:
                    field_fallback_value = None
                elif isinstance(model_fallback_values, dict):
                    field_fallback_value = model_fallback_values.get(
                        field_name, None)
                else:
                    field_fallback_value = model_fallback_values
                descriptor = TranslationFieldDescriptor(
                    model._meta.get_field(field_name),
                    fallback_value=field_fallback_value,
                    fallback_languages=model_fallback_languages)
                setattr(model, field_name, descriptor)

        #signals.pre_init.connect(translated_model_initializing, sender=model,
                                 #weak=False)

    def unregister(self, model_or_iterable):
        """
        Unregisters the given model(s).

        If a model isn't already registered, this will raise NotRegistered.
        """
        if isinstance(model_or_iterable, ModelBase):
            model_or_iterable = [model_or_iterable]
        for model in model_or_iterable:
            self.get_options_for_model(model).registered = False

    def _get_options_for_model(self, model, opts_class=None, **options):
        """
        Returns an instance of translation options with translated fields
        defined for the ``model`` and inherited from superclasses.
        """
        if model not in self._registry:
            # Create a new type for backwards compatibility.
            opts = type("%sTranslationOptions" % model.__name__,
                        (opts_class or TranslationOptions,), options)(model)

            # Fields for translation may be inherited from abstract
            # superclasses, so we need to look at all parents.
            for base in model.__bases__:
                if not hasattr(base, '_meta'):
                    # Things without _meta aren't functional models, so they're
                    # uninteresting parents.
                    continue
                opts.update(self._get_options_for_model(base))

            # Cache options for all models -- we may want to compute options
            # of registered subclasses of unregistered models.
            self._registry[model] = opts

        return self._registry[model]

    def get_options_for_model(self, model):
        """
        Thin wrapper around ``_get_options_for_model`` to preserve the
        semantic of throwing exception for models not directly registered.
        """
        opts = self._get_options_for_model(model)
        if not opts.registered:
            raise NotRegistered('The model "%s" is not registered for '
                                'translation' % model.__name__)
        return opts


# This global object represents the singleton translator object
translator = Translator()
