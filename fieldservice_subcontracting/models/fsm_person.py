# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class FsmPerson(models.Model):
    _inherit = "fsm.person"

    is_subcontractor = fields.Boolean()

    @api.constrains("is_subcontractor", "partner_id")
    def _check_subcontractor_is_vendor(self):
        for person in self:
            if person.is_subcontractor and person.partner_id.supplier_rank < 1:
                raise ValidationError(
                    self.env._(
                        "Worker '%(worker)s' is marked as subcontractor but "
                        "their partner '%(partner)s' is not configured as a "
                        "vendor. Please set the partner as a vendor first.",
                        worker=person.name,
                        partner=person.partner_id.name,
                    )
                )
