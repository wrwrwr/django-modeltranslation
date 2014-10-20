# -*- coding: utf-8 -*-
"""
Detect new translatable fields in all models and sync database structure.

You will need to execute this command in two cases:

    1. When you add new languages to settings.LANGUAGES.
    2. When you add new translatable fields to your models.

Credits: Heavily inspired by django-transmeta's sync_transmeta_db command.
"""
from optparse import make_option

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
            model_full_name = '%s.%s' % (model._meta.app_label, model._meta.module_name)
            opts = translator.get_options_for_model(model)
            for field_name, fields in opts.local_fields.items():
                field = list(fields)[0]
                column_name = field.db_column if field.db_column else field_name
                missing_langs = self.get_missing_languages(column_name, db_table)
                if missing_langs:
                    found_missing_fields = True
                    if self.verbosity > 0:
                        self.stdout.write('Missing languages in "%s" field from "%s" model: %s' % (
                            field_name, model_full_name, ', '.join(missing_langs)))
                    sql_sentences = self.get_sync_sql(field_name, missing_langs, model)
                    if self.interactive or self.verbosity > 0:
                        self.stdout.write('\nSQL to synchronize "%s" schema:' % model_full_name)
                        for sentence in sql_sentences:
                            self.stdout.write('   %s' % sentence)
                    if self.interactive:
                        answer = None
                        prompt = '\nAre you sure that you want to execute the printed SQL: (y/n) [n]: '
                        while answer not in ('', 'y', 'n', 'yes', 'no'):
                            answer = moves.input(prompt).strip()
                            prompt = 'Please answer yes or no: '
                        execute_sql = (answer == 'y' or answer == 'yes')
                    else:
                        execute_sql = True
                    if execute_sql:
                        if self.verbosity > 0:
                            self.stdout.write('Executing SQL...')
                        for sentence in sql_sentences:
                            self.cursor.execute(sentence)
                        if self.verbosity > 0:
                            self.stdout.write('Done')
                    else:
                        if self.verbosity > 0:
                            self.stdout.write('SQL not executed')

        transaction.commit_unless_managed()

        if self.verbosity > 0 and not found_missing_fields:
            self.stdout.write('No new translatable fields detected')

    def get_table_fields(self, db_table):
        """
        Gets table fields from schema.
        """
        db_table_desc = self.introspection.get_table_description(self.cursor, db_table)
        return [t[0] for t in db_table_desc]

    def get_missing_languages(self, column_name, db_table):
        """
        Returns codes of languages for which the given field does not have a
        translation field.
        """
        missing_langs = []
        db_table_fields = self.get_table_fields(db_table)
        for lang_code in AVAILABLE_LANGUAGES:
            if build_localized_fieldname(column_name, lang_code) not in db_table_fields:
                missing_langs.append(lang_code)
        return missing_langs

    def get_sync_sql(self, field_name, missing_langs, model):
        """
        Returns SQL needed for sync schema for a new translatable field.
        """
        qn = connection.ops.quote_name
        style = no_style()
        sql_output = []
        db_table = model._meta.db_table
        for lang in missing_langs:
            new_field = build_localized_fieldname(field_name, lang)
            f = model._meta.get_field(new_field)
            col_type = f.db_type(connection=connection)
            field_sql = [style.SQL_FIELD(qn(f.column)), style.SQL_COLTYPE(col_type)]
            stmt = "ALTER TABLE %s ADD COLUMN %s" % (qn(db_table), ' '.join(field_sql))
            if not f.null:
                stmt += " " + style.SQL_KEYWORD('NOT NULL')
            sql_output.append(stmt + ";")
        return sql_output
