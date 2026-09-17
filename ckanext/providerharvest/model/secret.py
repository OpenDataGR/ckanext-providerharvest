"""SQLAlchemy-backed SecretRepository for envelope-encrypted credentials.

Implements the narrow ``SecretRepository`` protocol from
``secrets.envelope`` -- the encryption logic itself has no idea this is
backed by CKAN's database, which is what keeps it unit-testable without one.
"""

from __future__ import annotations

import datetime
import uuid

from ckan.model.meta import Session

from ckanext.providerharvest.model.meta import mapper_registry, secret_table


class SecretRow:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id") or str(uuid.uuid4())
        for key, value in kwargs.items():
            setattr(self, key, value)


mapper_registry.map_imperatively(SecretRow, secret_table)


class SqlAlchemySecretRepository:
    def create(self, harvest_source_id: str, ciphertext: bytes,
               data_key_wrapped: bytes, algorithm: str) -> str:
        row = SecretRow(
            harvest_source_id=harvest_source_id,
            ciphertext=ciphertext,
            data_key_wrapped=data_key_wrapped,
            algorithm=algorithm,
            version=1,
        )
        Session.add(row)
        Session.commit()
        return row.id

    def get(self, secret_ref: str) -> dict:
        row = Session.query(SecretRow).get(secret_ref)
        if row is None:
            raise LookupError("No secret stored for ref %r" % secret_ref)
        return {
            "ciphertext": row.ciphertext,
            "data_key_wrapped": row.data_key_wrapped,
            "algorithm": row.algorithm,
            "harvest_source_id": row.harvest_source_id,
            "version": row.version,
        }

    def update(self, secret_ref: str, ciphertext: bytes, data_key_wrapped: bytes) -> None:
        row = Session.query(SecretRow).get(secret_ref)
        if row is None:
            raise LookupError("No secret stored for ref %r" % secret_ref)
        row.ciphertext = ciphertext
        row.data_key_wrapped = data_key_wrapped
        row.version = (row.version or 1) + 1
        row.rotated_at = datetime.datetime.utcnow()
        Session.commit()

    def delete(self, secret_ref: str) -> None:
        row = Session.query(SecretRow).get(secret_ref)
        if row is not None:
            Session.delete(row)
            Session.commit()
