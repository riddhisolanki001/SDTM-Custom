# Copyright (c) 2013, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.accounts.report.trial_balance.trial_balance import validate_filters


def execute(filters=None):
	validate_filters(filters)

	show_party_name = is_party_name_visible(filters)

	columns = get_columns(filters, show_party_name)
	data = get_data(filters, show_party_name)

	return columns, data


def get_data(filters, show_party_name):
	# 1. Decide party name field
	if filters.get("party_type") in ("Customer", "Supplier", "Employee", "Member"):
		party_name_field = f"{frappe.scrub(filters.party_type)}_name"
	elif filters.get("party_type") == "Shareholder":
		party_name_field = "title"
	else:
		party_name_field = "name"

	# 2. Get allowed parties (Sales Person / Territory)
	allowed_parties = get_allowed_parties(filters)

	# If filters applied but no customers match → empty report
	if allowed_parties is not None and not allowed_parties:
		return []

	# 🔑 VERY IMPORTANT
	filters.allowed_parties = allowed_parties

	# 3. Build party filters
	party_filters = {}

	if filters.get("party"):
		party_filters["name"] = filters.party

	if allowed_parties:
		party_filters["name"] = ["in", allowed_parties]

	# 4. Fetch parties
	parties = frappe.get_all(
		filters.party_type,
		fields=["name", party_name_field],
		filters=party_filters,
		order_by="name",
	)

	company_currency = frappe.get_cached_value(
		"Company", filters.company, "default_currency"
	)

	# 5. Fetch balances (NOW filters.allowed_parties is available)
	opening_balances = get_opening_balances(filters)
	balances_within_period = get_balances_within_period(filters)

	data = []

	total_row = frappe._dict({
		"opening_debit": 0,
		"opening_credit": 0,
		"debit": 0,
		"credit": 0,
		"closing_debit": 0,
		"closing_credit": 0,
	})

	# 6. Build rows
	for party in parties:
		row = {"party": party.name}

		if show_party_name:
			row["party_name"] = party.get(party_name_field)

		opening_debit, opening_credit = opening_balances.get(party.name, [0, 0])
		debit, credit = balances_within_period.get(party.name, [0, 0])

		closing_debit, closing_credit = toggle_debit_credit(
			opening_debit + debit,
			opening_credit + credit
		)

		row.update({
			"opening_debit": opening_debit,
			"opening_credit": opening_credit,
			"debit": debit,
			"credit": credit,
			"closing_debit": closing_debit,
			"closing_credit": closing_credit,
			"currency": company_currency,
		})

		for col in total_row:
			total_row[col] += row[col]

		if cint(filters.show_zero_values) or any([
			opening_debit, opening_credit, debit, credit, closing_debit, closing_credit
		]):
			data.append(row)

	# 7. Totals row
	total_row.update({
		"party": "'" + _("Totals") + "'",
		"currency": company_currency,
	})
	data.append(total_row)

	return data

def get_opening_balances(filters):
	account_filter = ""
	if filters.get("account"):
		account_filter = "and account = %(account)s"

	party_filter = ""
	if filters.get("allowed_parties"):
		party_filter = "and party in %(allowed_parties)s"

	gle = frappe.db.sql(
		f"""
		select
			party,
			sum(debit) as opening_debit,
			sum(credit) as opening_credit
		from `tabGL Entry`
		where company = %(company)s
			and is_cancelled = 0
			and ifnull(party_type, '') = %(party_type)s
			and ifnull(party, '') != ''
			and (
				posting_date < %(from_date)s
				or (
					ifnull(is_opening, 'No') = 'Yes'
					and posting_date <= %(to_date)s
				)
			)
			{account_filter}
			{party_filter}
		group by party
		""",
		{
			"company": filters.company,
			"party_type": filters.party_type,
			"from_date": filters.from_date,
			"to_date": filters.to_date,
			"account": filters.get("account"),
			"allowed_parties": tuple(filters.allowed_parties) if filters.get("allowed_parties") else None,
		},
		as_dict=True,
	)

	opening = frappe._dict()
	for d in gle:
		opening_debit, opening_credit = toggle_debit_credit(
			d.opening_debit, d.opening_credit
		)
		opening[d.party] = [opening_debit, opening_credit]

	return opening



def get_balances_within_period(filters):
	account_filter = ""
	if filters.get("account"):
		account_filter = "and account = %(account)s"

	party_filter = ""
	if filters.get("allowed_parties"):
		party_filter = "and party in %(allowed_parties)s"

	gle = frappe.db.sql(
		f"""
		select
			party,
			sum(debit) as debit,
			sum(credit) as credit
		from `tabGL Entry`
		where company = %(company)s
			and is_cancelled = 0
			and ifnull(party_type, '') = %(party_type)s
			and ifnull(party, '') != ''
			and posting_date >= %(from_date)s
			and posting_date <= %(to_date)s
			and ifnull(is_opening, 'No') = 'No'
			{party_filter}
			{account_filter}
		group by party
		""",
		{
			"company": filters.company,
			"party_type": filters.party_type,
			"from_date": filters.from_date,
			"to_date": filters.to_date,
			"account": filters.get("account"),
			"allowed_parties": tuple(filters.allowed_parties) if filters.get("allowed_parties") else None,
		},
		as_dict=True,
	)

	balances_within_period = frappe._dict()
	for d in gle:
		balances_within_period[d.party] = [d.debit, d.credit]

	return balances_within_period



def toggle_debit_credit(debit, credit):
	if flt(debit) > flt(credit):
		debit = flt(debit) - flt(credit)
		credit = 0.0
	else:
		credit = flt(credit) - flt(debit)
		debit = 0.0

	return debit, credit


def get_columns(filters, show_party_name):
	columns = [
		{
			"fieldname": "party",
			"label": _(filters.party_type),
			"fieldtype": "Link",
			"options": filters.party_type,
			"width": 200,
		},
		{
			"fieldname": "opening_debit",
			"label": _("Opening (Dr)"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "opening_credit",
			"label": _("Opening (Cr)"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "debit",
			"label": _("Debit"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "credit",
			"label": _("Credit"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "closing_debit",
			"label": _("Closing (Dr)"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "closing_credit",
			"label": _("Closing (Cr)"),
			"fieldtype": "Currency",
			"options": "currency",
			"width": 120,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
		},
	]

	if show_party_name:
		columns.insert(
			1,
			{
				"fieldname": "party_name",
				"label": _(filters.party_type) + " Name",
				"fieldtype": "Data",
				"width": 200,
			},
		)

	return columns


def is_party_name_visible(filters):
	show_party_name = False

	if filters.get("party_type") in ["Customer", "Supplier"]:
		if filters.get("party_type") == "Customer":
			party_naming_by = frappe.db.get_single_value("Selling Settings", "cust_master_name")
		else:
			party_naming_by = frappe.db.get_single_value("Buying Settings", "supp_master_name")

		if party_naming_by == "Naming Series":
			show_party_name = True
	else:
		show_party_name = True

	return show_party_name

def get_allowed_parties(filters):
	if filters.party_type != "Customer":
		return None

	conditions = []
	values = {}

	# Territory filter
	if filters.get("territory"):
		conditions.append("c.territory = %(territory)s")
		values["territory"] = filters.territory

	# Sales Person filter
	if filters.get("sales_person"):
		conditions.append("""
			exists (
				select 1 from `tabSales Team` st
				where st.parent = c.name
				and st.parenttype = 'Customer'
				and st.sales_person = %(sales_person)s
			)
		""")
		values["sales_person"] = filters.sales_person

	if not conditions:
		return None

	query = f"""
		select c.name
		from `tabCustomer` c
		where {" and ".join(conditions)}
	"""

	return frappe.db.sql(query, values, pluck="name")
