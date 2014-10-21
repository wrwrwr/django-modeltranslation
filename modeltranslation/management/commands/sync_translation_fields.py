# -*- coding: utf-8 -*-
"""
Detect new translatable fields in all models and sync database structure.

You will need to execute this command in two cases:

    1. When you add new languages to settings.LANGUAGES.
    2. When you add new translatable fields to your models.

Credits: Heavily inspired by django-transmeta's sync_transmeta_db command.
"""
from optparse import make_option

import django
from django.core.management.base import NoArgsCommand
from django.core.management.color import no_style
from django.db import connection, transaction
from django.utils.six import moves

from modeltranslation.settings import AVAILABLE_LANGUAGES
from modeltranslation.translator import translator
from modeltranslation.utils import build_localized_fieldname


class Command(NoArgsCommand):
    help = ('Detect new translatable fields or new available languages and'
            ' sync database structure. Does not remove columns of removed'
            ' languages or undeclared fields.')

    option_list = NoArgsCommand.option_list + (
        make_option('--noinput', action='store_false', dest='interactive', default=True,
                    help='Do NOT prompt the user for input of any kind.'),
    )

    def handle_noargs(self, **options):
        self.cursor = connection.cursor()
        self.introspection = connection.introspection
        self.interactive = options['interactive']
        self.verbosity = int(options.get('verbosity'))

        found_missing_fields = False
        models = translator.get_registered_models(abstract=False)
        for model in models:
            db_table = model._meta.db_table
            model_full_name = '%s.%s' % (model._meta.app_label, model._meta.object_name)
            opts = translator.get_options_for_model(model)
            for field_name, fields in opts.local_fields.items():
                field = list(fields)[0]
                db_column = field.db_column if field.db_column else field_name
                missing_langs = self.find_missing_languages(db_column, db_table)
                if not missing_langs:
                    continue
                found_missing_fields = True
                field_full_name = '%s.%s' % (model_full_name, field_name)
                if self.verbosity > 0:
                    self.stdout.write('Missing translation columns for field "%s": %s' % (
                        field_full_name, ', '.join(missing_langs)))
                statements = self.generate_add_column_statements(field_name, missing_langs, model)
                if self.interactive or self.verbosity > 0:
                    self.stdout.write('\nStatements to be executed for "%s":' % field_full_name)
                    for statement in statements:
                        self.stdout.write('   %s' % statement)
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

        if django.VERSION < (1, 6) and found_missing_fields:
            transaction.commit_unless_managed()

        if self.verbosity > 0 and not found_missing_fields:
            self.stdout.write('No new translatable fields detected')

    def find_missing_languages(self, db_column, db_table):
        """
        Returns codes of languages for which the given field doesn't have a
        translation column in the database.
        """
        missing_langs = []
        table_description = self.introspection.get_table_description(self.cursor, db_table)
        table_columns = (t[0] for t in table_description)
        for lang_code in AVAILABLE_LANGUAGES:
            lang_column = build_localized_fieldname(db_column, lang_code)
            if lang_column not in table_columns:
                missing_langs.append(lang_code)
        return missing_langs

    def generate_add_column_statements(self, field_name, missing_langs, model):
        """
        Returns database statements needed to add missing columns for the
        field.
        """
        statements = []
        style = no_style()
        qn = connection.ops.quote_name
        db_table = model._meta.db_table
        for lang in missing_langs:
            new_field_name = build_localized_fieldname(field_name, lang)
            new_field = model._meta.get_field(new_field_name)
            db_column = new_field.column
            db_column_type = new_field.db_type(connection=connection)
            statement = 'ALTER TABLE %s ADD COLUMN %s %s' % (qn(db_table),
                                                             style.SQL_FIELD(qn(db_column)),
                                                             style.SQL_COLTYPE(db_column_type))
            if not new_field.null:
                statement += ' ' + style.SQL_KEYWORD('NOT NULL')
            statements.append(statement + ';')
        return statements
