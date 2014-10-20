# -*- coding: utf-8 -*-
from django.db.models import F, Q
from django.core.management.base import BaseCommand

from modeltranslation.settings import DEFAULT_LANGUAGE
from modeltranslation.translator import translator
from modeltranslation.utils import build_localized_fieldname


class Command(BaseCommand):
    help = ('Updates empty values of default translation fields using'
            ' values from original fields (in all translated models).')

    def add_arguments(self, parser):
        parser.add_argument('--app', '--app_config', default=None,
                            help='Limit updating values to a single app.')

    def handle(self, **options):
        self.verbosity = int(options['verbosity'])
        self.app_config = options.get('app')
        if self.app_config is not None:
            from django.apps import AppConfig, apps
            if not isinstance(self.app_config, AppConfig):
                self.app_config = apps.get_app_config(self.app_config)

        if self.verbosity > 0:
            self.stdout.write("Using default language: {}\n".format(DEFAULT_LANGUAGE))
        models = translator.get_registered_models(abstract=False, app_config=self.app_config)
        for model in models:
            if self.verbosity > 0:
                self.stdout.write("Updating data of model '{}'\n".format(model))
            opts = translator.get_options_for_model(model)
            for field_name in opts.fields.keys():
                def_lang_fieldname = build_localized_fieldname(field_name, DEFAULT_LANGUAGE)

                # We'll only update fields which do not have an existing value
                q = Q(**{def_lang_fieldname: None})
                field = model._meta.get_field(field_name)
                if field.empty_strings_allowed:
                    q |= Q(**{def_lang_fieldname: ''})

                model._default_manager.filter(q).rewrite(False).update(
                    **{def_lang_fieldname: F(field_name)})
