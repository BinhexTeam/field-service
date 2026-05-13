# Copyright 2026 Binhex
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FsmOrder(models.Model):
    _inherit = "fsm.order"

    purchase_order_ids = fields.One2many(
        comodel_name="purchase.order",
        inverse_name="fsm_order_id",
        string="Subcontract POs",
        help="Purchase Order auto-created when this FSO was assigned "
        "to a subcontractor worker.",
    )

    purchase_order_count = fields.Integer(
        compute="_compute_purchase_order_count",
    )

    reassign_worker = fields.Boolean(
        compute="_compute_reassign_worker",
        help="Determine if a worker reassignment can be performed.",
    )

    @api.depends("purchase_order_ids")
    def _compute_purchase_order_count(self):
        for order in self:
            order.purchase_order_count = len(order.purchase_order_ids)

    @api.depends("purchase_order_ids", "stage_id.is_closed")
    def _compute_reassign_worker(self):
        for order in self:
            order.reassign_worker = order._can_reassign_subcontract_worker()

    def _can_reassign_subcontract_worker(self):
        self.ensure_one()
        return bool(self.purchase_order_ids) and not self.stage_id.is_closed

    def _check_reassign_subcontract_worker_allowed(self):
        self.ensure_one()
        if self.stage_id.is_closed:
            raise UserError(
                self.env._(
                    "You cannot reassign the worker because the Field Service "
                    "Order is in a closed stage."
                )
            )
        if not self.purchase_order_ids:
            raise UserError(self.env._("There is no subcontract Purchase Order."))

    def _get_active_subcontract_purchase_orders(self):
        return self.purchase_order_ids.filtered(lambda po: po.state != "cancel")

    def _get_posted_subcontract_vendor_bills(self):
        return self._get_active_subcontract_purchase_orders().invoice_ids.filtered(
            lambda invoice: invoice.state == "posted"
        )

    def _check_subcontract_purchase_orders_can_be_cancelled(self):
        posted_bills = self._get_posted_subcontract_vendor_bills()
        if posted_bills:
            raise UserError(
                self.env._(
                    "Cannot cancel Purchase Orders because they have %(count)d "
                    "posted vendor bill(s). Please manage the POs manually first.",
                    count=len(posted_bills),
                )
            )

    def _cancel_active_subcontract_purchase_orders(self):
        purchase_orders = self._get_active_subcontract_purchase_orders()
        if purchase_orders:
            self._check_subcontract_purchase_orders_can_be_cancelled()
            purchase_orders.button_cancel()
        return purchase_orders

    def action_view_purchase_order(self):
        """Smart button action to open linked Purchase Orders."""
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [("id", "in", self.purchase_order_ids.ids)],
            "target": "current",
        }
        if len(self.purchase_order_ids) == 1:
            action.update(
                {
                    "res_id": self.purchase_order_ids.id,
                    "view_mode": "form",
                }
            )
        return action

    def action_open_reassign_confirm(self):
        """Open the reassignment confirmation wizard."""
        self.ensure_one()
        self._check_reassign_subcontract_worker_allowed()
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirm Worker Reassignment"),
            "res_model": "fsm.order.reassign.confirm",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_fsm_order_id": self.id,
            },
        }

    def action_cancel(self):
        """Ask how to handle active subcontract POs before cancelling the FSO."""
        if not self.env.context.get("skip_subcontract_cancel_wizard"):
            orders_with_purchase = self.filtered(
                lambda order: order._get_active_subcontract_purchase_orders()
            )
            if orders_with_purchase:
                if len(self) > 1:
                    raise UserError(
                        self.env._(
                            "Cancel Field Service Orders with active subcontract "
                            "Purchase Orders one at a time."
                        )
                    )
                return {
                    "type": "ir.actions.act_window",
                    "name": self.env._("Cancel Field Service Order"),
                    "res_model": "fsm.order.cancel.confirm",
                    "view_mode": "form",
                    "target": "new",
                    "context": {
                        "default_fsm_order_id": orders_with_purchase.id,
                    },
                }
        return super().action_cancel()

    def _create_subcontract_po(self):
        """Create a draft Purchase Order for the subcontractor.

        Called by the server action linked to the 'Assigned' stage
        (or equivalent). Only creates a PO if:
        - The assigned worker is a subcontractor
        - The FSO template has a subcontracting product configured.
        - No PO is already linked to this FSO

        Errors are logged to the chatter instead of raising exceptions,
        to avoid blocking the stage transition.
        """
        message_no_subcontracting = self.env._(
            "The subcontracting purchase order cannot be created:"
        )
        for order in self:
            if order._get_active_subcontract_purchase_orders():
                continue
            subcontracting_errors = []
            if not order.person_id or not order.person_id.is_subcontractor:
                if not order.person_id:
                    subcontracting_errors.append(
                        self.env._("It does not have an assigned worker.")
                    )
                elif not order.person_id.is_subcontractor:
                    subcontracting_errors.append(
                        self.env._(
                            "The assigned worker '%(worker)s' is not a subcontractor.",
                            worker=order.person_id.name,
                        )
                    )
            if not order.type or not order.template_id.subcontract_product_id:
                subcontracting_errors.append(
                    self.env._(
                        "The outsourcing product is not configured in "
                        "the template '%(template_name)s'.",
                        template_name=order.template_id.name
                        if order.template_id
                        else self.env._("(none)"),
                    )
                )

            partner_id = order.person_id.partner_id
            if (
                order.person_id
                and order.person_id.is_subcontractor
                and partner_id.supplier_rank < 1
            ):
                subcontracting_errors.append(
                    self.env._(
                        "Partner '%(partner)s' associated with the worker is "
                        "not associated as a supplier.",
                        partner=partner_id.name,
                    )
                )

            if subcontracting_errors:
                body = Markup("%s<ul><li>%s</li></ul>") % (
                    message_no_subcontracting,
                    Markup("</li><li>").join(subcontracting_errors),
                )
                order.message_post(body=body)
                continue

            purchase_order_vals = order._prepare_subcontract_po_vals()
            purchase_order_id = self.env["purchase.order"].create(purchase_order_vals)
            order.message_post(
                body=self.env._(
                    "Subcontract Purchase Order: "
                    " %(link_purchase_order)s "
                    "created for vendor %(vendor)s.",
                    link_purchase_order=purchase_order_id._get_html_link(),
                    vendor=partner_id.name,
                ),
            )

    def _prepare_subcontract_po_vals(self):
        """
        Prepare the values dict for the subcontract Purchase Order.
        :return: Dict of values
        """
        self.ensure_one()
        product = self.template_id.subcontract_product_id
        partner_id = self.person_id.partner_id
        analytic_distribution = {}
        if self.project_id and self.project_id.account_id:
            analytic_distribution[str(self.project_id.account_id.id)] = 100.0

        purchase_line_vals = {
            "product_id": product.id,
            "name": self.env._("Subcontracted service: %(fso)s", fso=self.name),
            "product_qty": 1.0,
            "product_uom": product.uom_id.id,
        }
        if analytic_distribution:
            purchase_line_vals["analytic_distribution"] = analytic_distribution

        return {
            "partner_id": partner_id.id,
            "fsm_order_id": self.id,
            "project_id": self.project_id.id,
            "origin": self.name,
            "order_line": [(0, 0, purchase_line_vals)],
        }

    def _update_subcontract_po_qty(self):
        """Update PO line qty_received with actual FSO timesheet hours.

        Called by the server action linked to the 'Done' stage.
        The source of hours is agnostic — they may be logged by
        internal staff or by the vendor.
        """
        for order in self:
            purchase_order_ids = order._get_active_subcontract_purchase_orders()
            if not purchase_order_ids:
                continue
            total_hours = sum(order.mapped("timesheet_ids.unit_amount"))
            if not order.type or not order.template_id.subcontract_product_id:
                continue
            for purchase_order in purchase_order_ids:
                po_line = purchase_order.order_line.filtered(
                    lambda line, product=order.template_id.subcontract_product_id: (
                        line.product_id == product
                    )
                )[:1]
                if po_line:
                    po_line.sudo().write(
                        {
                            "product_qty": total_hours,
                            "qty_received": total_hours,
                        }
                    )
                    purchase_order.message_post(
                        body=self.env._(
                            "Delivered quantity updated to %(hours).2f hours "
                            "from FSO %(fso)s.",
                            hours=total_hours,
                            fso=order.name,
                        ),
                    )

    def write(self, vals):
        if "person_id" in vals and not self.env.context.get("skip_reassign_check"):
            protected_orders = self.filtered("purchase_order_ids")
            if protected_orders:
                raise UserError(
                    self.env._(
                        "You cannot directly reassign an order with a "
                        "subcontract Purchase Order. Use the Reassign Worker "
                        "button instead."
                    )
                )
        return super().write(vals)
