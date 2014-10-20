# -*- coding: utf-8 -*-
"""
Detect new translatable fields in all models and sync database structure.

You will need to execute this command in two cases:

    1. When you add new languages to settings.LANGUAGES.
    2. When you add new translatable fields to your models.

Credits: Heavily inspired by django-transmeta's sync_transmeta_db command.
"""
from django.core.management.base import BaseCommand
from django.core.management.color import no_style
from django.db import connection
from django.utils.six import moves

from modeltranslation.settings import AVAILABLE_LANGUAGES
from modeltranslation.translator import translator
from modeltranslation.utils import build_localized_fieldname


class Command(BaseCommand):
    help = ('Detect new translatable fields or new available languages and'
            ' sync database structure. Does not remove columns of removed'
            ' languages or undeclared fields.')

    def add_arguments(self, parser):
        parser.add_argument('--noinput', action='store_false', default=True,
                            help='Do NOT prompt the user for input of any kind.')
        parser.add_argument('--app', '--app_config', default=None,
                            help='Limit looking for missing columns to a single app.')

    def handle(self, **options):
        self.cursor = connection.cursor()
        self.introspection = connection.introspection
        self.interactive = options['interactive']
        self.verbosity = int(options['verbosity'])
        self.app_config = options.get('app')
        if self.app_config is not None:
            from django.apps import AppConfig, apps
            if not isinstance(self.app_config, AppConfig):
                self.app_config = apps.get_app_config(self.app_config)

        found_missing_columns = False
        models = translator.get_registered_models(abstract=False, app_config=self.app_config)
        for model in models:
            db_table = model._meta.db_table
            model_full_name = '{0.app_label}.{0.object_name}'.format(model._meta)

            opts = translator.get_options_for_model(model)
            for field_name in opts.local_fields.keys():
                field = model._meta.get_field(field_name)

                missing_columns = self.find_missing_columns(field, db_table)
                if not missing_columns:
                    continue
                found_missing_columns = True
                field_full_name = '{}.{}'.format(model_full_name, field_name)
                if self.verbosity > 0:
                    self.stdout.write('Missing translation columns for field "{}": {}'.format(
                        field_full_name, ', '.join(missing_columns.keys())))

                statements = self.generate_add_column_statements(field, missing_columns, model)
                if self.interactive or self.verbosity > 0:
                    self.stdout.write('\nStatements to be executed for "{}":'.format(field_full_name))
                    for statement in statements:
                        self.stdout.write('   {}'.format(statement))
                if self.interactive:
                    answer = None
                    prompt = ('\nAre you sure that you want to execute the printed statements:'
                              ' (y/n) [n]: ')
                    while answer not in ('', 'y', 'n', 'yes', 'no'):
                        answer = moves.input(prompt).strip()
                        prompt = 'Please answer yes or no: '
                    execute = (answer == 'y' or answer == 'yes')
                else:
                    execute = True
                if execute:
                    if self.verbosity > 0:
                        self.stdout.write('Executing statements...')
                    for statement in statements:
                        self.cursor.execute(statement)
                    if self.verbosity > 0:
                        self.stdout.write('Done')
                else:
                    if self.verbosity > 0:
                        self.stdout.write('Statements not executed')

        if self.verbosity > 0 and not found_missing_columns:
            self.stdout.write('No new translatable fields detected')

    def find_missing_columns(self, field, db_table):
        """
        Returns a dictionary of (code, column name) for languages for which
        the given field doesn't have a translation column in the database.
        """
        missing_columns = {}
        db_column = field.db_column if field.db_column else field.name
        db_table_description = self.introspection.get_table_description(self.cursor, db_table)
        db_table_columns = [t[0] for t in db_table_description]
        for lang_code in AVAILABLE_LANGUAGES:
            lang_column = build_localized_fieldname(db_column, lang_code)
            if lang_column not in db_table_columns:
                missing_columns[lang_code] = lang_column
        return missing_columns

    def generate_add_column_statements(self, field, missing_columns, model):
        """
        Returns database statements needed to add missing columns for the
        field.
        """
        statements = []
        style = no_style()
        qn = connection.ops.quote_name
        db_table = model._meta.db_table
        db_column_type = field.db_type(connection=connection)
        for lang_column in missing_columns.values():
            statement = 'ALTER TABLE {} ADD COLUMN {} {}'.format(qn(db_table),
                                                                 style.SQL_FIELD(qn(lang_column)),
                                                                 style.SQL_COLTYPE(db_column_type))
            if not model._meta.get_field(lang_column).null:
                # Just "not field.null" if we change the nullability politics.
                statement += ' ' + style.SQL_KEYWORD('NOT NULL')
            statements.append(statement + ';')
        return statements
