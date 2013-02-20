# -*- coding: utf-8 -*-
"""
The idea of MultilingualManager is taken from
django-linguo by Zach Mathew

https://github.com/zmathew/django-linguo
"""
from __future__ import with_statement  # Python 2.5 compatibility
from django.db import models
from django.db.models import Q
from django.db.models.fields.related import RelatedField
from django.db.models.sql.constants import QUERY_TERMS
from django.db.models.sql.where import Constraint
from django.utils.tree import Node

from modeltranslation import settings
from modeltranslation.utils import (build_localized_fieldname, get_language,
                                    auto_populate, resolution_order)


def get_translatable_fields_for_model(model):
    from modeltranslation.translator import NotRegistered, translator
    try:
        return translator.get_options_for_model(model).fields
    except NotRegistered:
        return None


def rewrite_lookup_key(model, lookup_key, language=None):
    if language is None:
        language = get_language()
    translatable_fields = get_translatable_fields_for_model(model)
    if translatable_fields is not None:
        pieces = lookup_key.split('__')
        # If we are doing a lookup on a translatable field,
        # we want to rewrite it to the actual field name
        # For example, we want to rewrite "name__startswith" to "name_fr__startswith"
        if pieces[0] in translatable_fields:
            lookup_key = build_localized_fieldname(pieces[0], language)
            remaining_lookup = '__'.join(pieces[1:])
            if remaining_lookup:
                lookup_key = '%s__%s' % (lookup_key, remaining_lookup)

    pieces = lookup_key.split('__')
    if len(pieces) > 1:
        # Check if we are doing a lookup to a related trans model
        fields_to_trans_models = get_fields_to_translatable_models(model)
        for field_to_trans, transmodel in fields_to_trans_models:
            if pieces[0] == field_to_trans:
                sub_lookup = '__'.join(pieces[1:])
                if sub_lookup:
                    sub_lookup = rewrite_lookup_key(transmodel, sub_lookup)
                    lookup_key = '%s__%s' % (pieces[0], sub_lookup)
                break

    return lookup_key


def _rewrite_with_fallbacks(model, lookup_fields, lookup_type, value, langs):
    fields = rewrite_lookup_key(model, lookup_fields, langs[0])
    isnull = '%s__isnull' % fields
    lookup = '%s__%s' % (fields, lookup_type)
    q = Q(**{isnull: False}) & Q(**{lookup: value})
    if len(langs) > 1:
        empty = Q(**{isnull: True}) | Q(**{fields: ''})
        fallback = _rewrite_with_fallbacks(model, lookup_fields, lookup_type, value, langs[1:])
        q |= empty & fallback
    return q

def rewrite_with_fallbacks(model, lookup, value):
    """
    Turns a lookup into a Q object comparing against the first language for
    which there is a non-empty value.

    Example:

        Suppose that the current language 'de' should fall back to 'en' and
        then to 'es', then:

            rewrite_with_fallbacks(model, 'title__startswith', 'foo')

        would give:

            title_de starts with 'foo' or (title_de is empty, but
                (title_en starts with 'foo' or (title_en is empty, but
                    (title_es starts with 'foo')))

        represented by a Q object, with value comparisons strenthened by
        a non-null check to handle negation.
    """
    parts = lookup.rsplit('__', 1)
    lookup_fields = parts[0]
    lookup_type = 'exact'
    if len(parts) > 1 and parts[1] in QUERY_TERMS:
        lookup_type = parts[1]
    if lookup_type == 'isnull':
        # Would require handling negation in a different manner.
        raise NotImplementedError('Fallbacks are not supported with isnull.')
    return _rewrite_with_fallbacks(model, lookup_fields, lookup_type, value,
                                   resolution_order(get_language()))


def rewrite_order_lookup_key(model, lookup_key):
    if lookup_key.startswith('-'):
        return '-' + rewrite_lookup_key(model, lookup_key[1:])
    else:
        return rewrite_lookup_key(model, lookup_key)


def get_fields_to_translatable_models(model):
    from modeltranslation.translator import translator
    results = []
    for field_name in translator.get_options_for_model(model).fields.keys():
        field_object, modelclass, direct, m2m = model._meta.get_field_by_name(field_name)
        if direct and isinstance(field_object, RelatedField):
            if get_translatable_fields_for_model(field_object.related.parent_model) is not None:
                results.append((field_name, field_object.related.parent_model))
    return results


class MultilingualQuerySet(models.query.QuerySet):

    def __init__(self, *args, **kwargs):
        super(MultilingualQuerySet, self).__init__(*args, **kwargs)
        self._post_init()

    def _post_init(self):
        # MultilingualManager.get_query_set overrides __class__ attribute,
        # so __init__ isn't guaranteed to be executed.
        self._rewrite = True
        self._fallbacks = False
        self._populate = None

        if self.model and (not self.query.order_by):
            if self.model._meta.ordering:
                # If we have default ordering specified on the model, set it now so that
                # it can be rewritten. Otherwise sql.compiler will grab it directly from _meta
                ordering = []
                for key in self.model._meta.ordering:
                    ordering.append(rewrite_order_lookup_key(self.model, key))
                self.query.add_ordering(*ordering)

    # This method was not present in django-linguo
    def _clone(self, *args, **kwargs):
        kwargs.setdefault('_rewrite', self._rewrite)
        kwargs.setdefault('_fallbacks', self._fallbacks)
        kwargs.setdefault('_populate', self._populate)
        return super(MultilingualQuerySet, self)._clone(*args, **kwargs)

    # This method was not present in django-linguo
    def rewrite(self, mode=True):
        """
        Allows to disable any rewriting, thus making the query set
        behave almost like a standard Django ``QuerySet``.
        """
        return self._clone(_rewrite=mode)

    # This method was not present in django-linguo
    def fallbacks(self, enable=True):
        """
        Enable rewriting of some queries to account for fallback languages.

        On default no fallbacks are considered in lookups / filtering
        as applying them to all queries could cause some significant overhead.
        """
        return self._clone(_fallbacks=enable)

    def populate(self, mode='all'):
        """
        Overrides the translation fields population mode for this query set.
        """
        return self._clone(_populate=mode)

    def _rewrite_applied_operations(self):
        """
        Rewrite fields in already applied filters/ordering.
        Useful when converting any QuerySet into MultilingualQuerySet.
        """
        self._rewrite_where(self.query.where)
        self._rewrite_where(self.query.having)
        self._rewrite_order()

    def _rewrite_where(self, q):
        """
        Rewrite field names inside WHERE tree.
        """
        if isinstance(q, tuple) and isinstance(q[0], Constraint):
            c = q[0]
            new_name = rewrite_lookup_key(self.model, c.field.name)
            if c.field.name != new_name:
                c.field = self.model._meta.get_field(new_name)
                c.col = c.field.column
        if isinstance(q, Node):
            map(self._rewrite_where, q.children)

    def _rewrite_order(self):
        self.query.order_by = [rewrite_order_lookup_key(self.model, field_name)
                               for field_name in self.query.order_by]

    # This method was not present in django-linguo
    def _rewrite_q(self, q):
        """Rewrite field names inside Q call."""
        if isinstance(q, tuple) and len(q) == 2:
            if self._fallbacks and settings.ENABLE_FALLBACKS:
                return rewrite_with_fallbacks(self.model, q[0], q[1])
            else:
                return rewrite_lookup_key(self.model, q[0]), q[1]
        if isinstance(q, Node):
            q.children = map(self._rewrite_q, q.children)
        return q

    # This method was not present in django-linguo
    def _rewrite_f(self, q):
        """
        Rewrite field names inside F call.
        """
        if isinstance(q, models.F):
            q.name = rewrite_lookup_key(self.model, q.name)
            return q
        if isinstance(q, Node):
            q.children = map(self._rewrite_f, q.children)
        return q

    def _filter_or_exclude(self, negate, *args, **kwargs):
        if self._rewrite:
            return super(MultilingualQuerySet, self)._filter_or_exclude(
                negate, self._rewrite_q(Q(*args, **kwargs)))
        else:
            return super(MultilingualQuerySet, self)._filter_or_exclude(
                negate, *args, **kwargs)

    def order_by(self, *field_names):
        """
        Change translatable field names in an ``order_by`` argument
        to translation fields for the current language.
        """
        if not self._rewrite:
            return super(MultilingualQuerySet, self).order_by(*field_names)
        new_args = []
        for key in field_names:
            new_args.append(rewrite_order_lookup_key(self.model, key))
        return super(MultilingualQuerySet, self).order_by(*new_args)

    def update(self, **kwargs):
        if not self._rewrite:
            return super(MultilingualQuerySet, self).update(**kwargs)
        for key, val in kwargs.items():
            new_key = rewrite_lookup_key(self.model, key)
            del kwargs[key]
            kwargs[new_key] = self._rewrite_f(val)
        return super(MultilingualQuerySet, self).update(**kwargs)
    update.alters_data = True

    # This method was not present in django-linguo
    @property
    def _populate_mode(self):
        # Populate can be set using a global setting or a manager method.
        if self._populate is None:
            return settings.AUTO_POPULATE
        return self._populate

    # This method was not present in django-linguo
    def create(self, **kwargs):
        """
        Allows to override population mode with a ``populate`` method.
        """
        with auto_populate(self._populate_mode):
            return super(MultilingualQuerySet, self).create(**kwargs)

    # This method was not present in django-linguo
    def get_or_create(self, **kwargs):
        """
        Allows to override population mode with a ``populate`` method.
        """
        with auto_populate(self._populate_mode):
            return super(MultilingualQuerySet, self).get_or_create(**kwargs)


class MultilingualManager(models.Manager):
    use_for_related_fields = True

    def rewrite(self, *args, **kwargs):
        return self.get_query_set().rewrite(*args, **kwargs)

    def fallbacks(self, *args, **kwargs):
        return self.get_query_set().fallbacks(*args, **kwargs)

    def populate(self, *args, **kwargs):
        return self.get_query_set().populate(*args, **kwargs)

    def get_query_set(self):
        qs = super(MultilingualManager, self).get_query_set()
        if qs.__class__ == models.query.QuerySet:
            qs.__class__ = MultilingualQuerySet
        else:
            class NewClass(qs.__class__, MultilingualQuerySet):
                pass
            NewClass.__name__ = 'Multilingual%s' % qs.__class__.__name__
            qs.__class__ = NewClass
        qs._post_init()
        qs._rewrite_applied_operations()
        return qs
