"""
FA2 library with the new SmartPy syntax.
"""

import smartpy as sp


@sp.module
def t():
    operator_permission: type = sp.record(
        owner=sp.address, operator=sp.address, token_id=sp.nat
    ).layout(("owner", ("operator", "token_id")))

    update_operators_params: type = list[
        sp.variant(
            add_operator=operator_permission, remove_operator=operator_permission
        )
    ]

    tx: type = sp.record(
        to_=sp.address,
        token_id=sp.nat,
        amount=sp.nat,
    ).layout(("to_", ("token_id", "amount")))

    transfer_batch: type = sp.record(
        from_=sp.address,
        txs=list[tx],
    ).layout(("from_", "txs"))

    transfer_params: type = list[transfer_batch]

    balance_of_request: type = sp.record(owner=sp.address, token_id=sp.nat).layout(
        ("owner", "token_id")
    )

    balance_of_response: type = sp.record(
        request=balance_of_request, balance=sp.nat
    ).layout(("request", "balance"))

    balance_of_params: type = sp.record(
        callback=sp.contract[list[balance_of_response]],
        requests=list[balance_of_request],
    ).layout(("requests", "callback"))

    balance_params: type = sp.pair[
        sp.lambda_(sp.nat, sp.bool, with_storage="read-only"), balance_of_request
    ]

    token_metadata: type = sp.big_map[
        sp.nat,
        sp.record(token_id=sp.nat, token_info=sp.map[sp.string, sp.bytes]).layout(
            ("token_id", "token_info")
        ),
    ]

    metadata: type = sp.big_map[sp.string, sp.bytes]

    ledger_nft: type = sp.big_map[sp.nat, sp.address]

    ledger_fungible: type = sp.big_map[sp.pair[sp.address, sp.nat], sp.nat]

    ledger_single_asset: type = sp.big_map[sp.address, sp.nat]

    supply_fungible: type = sp.big_map[sp.nat, sp.nat]

    tx_transfer_permission: type = sp.record(
        from_=sp.address, to_=sp.address, token_id=sp.nat
    )


@sp.module
def main():
    import t

    ############
    # Policies #
    ############

    class OwnerOrOperatorTransfer(sp.Contract):
        """Owner or operator are allowed to transfer."""

        def __init__(self):
            self.private.policy = sp.record(
                name="owner-or-operator-transfer",
                supports_transfer=True,
                supports_operator=True,
            )
            self.data.operators = sp.cast(
                sp.big_map(), sp.big_map[t.operator_permission, sp.unit]
            )

        @sp.private()
        def check_operator_update_permissions_(self, operator_permission):
            sp.cast(operator_permission, t.operator_permission)
            assert operator_permission.owner == sp.sender, "FA2_NOT_OWNER"

        @sp.private(with_storage="read-only")
        def check_tx_transfer_permissions_(self, params):
            sp.cast(params, t.tx_transfer_permission)
            sp.cast(self.data.operators, sp.big_map[t.operator_permission, sp.unit])
            assert (sp.sender == params.from_) or self.data.operators.contains(
                sp.record(
                    owner=params.from_, operator=sp.sender, token_id=params.token_id
                )
            ), "FA2_NOT_OPERATOR"

        @sp.private(with_storage="read-only")
        def is_operator_(self, operator_permission):
            sp.cast(self.data.operators, sp.big_map[t.operator_permission, sp.unit])
            return self.data.operators.contains(operator_permission)

    ##########
    # Common #
    ##########

    class CommonInterface(OwnerOrOperatorTransfer):
        def __init__(self):
            OwnerOrOperatorTransfer.__init__(self)
            self.data.token_metadata = sp.cast(sp.big_map(), t.token_metadata)
            self.data.metadata = sp.cast(sp.big_map(), t.metadata)
            self.data.next_token_id = 0

        @sp.private()
        def balance_(self, params):
            """Return the balance of an account.
            Must be redefined in child"""
            sp.cast(params, t.balance_params)
            raise "NotImplemented"
            return 0

        @sp.private()
        def is_defined_(self, token_id):
            """Return True if the token is defined, else otherwise.
            Must be redefined in child"""
            sp.cast(token_id, sp.nat)
            raise "NotImplemented"
            return False

        @sp.private()
        def transfer_tx_(self, params):
            """Perform the transfer action.
            Must be redefined in child"""
            sp.cast(params, sp.record(from_=sp.address, tx=t.tx))
            raise "NotImplemented"
            return ()

        @sp.private()
        def supply_(self, params):
            """Return the supply of a token.
            Must be redefined in child"""
            (is_defined, token_id) = params
            sp.cast(token_id, sp.nat)
            raise "NotImplemented"
            return 0

    class Common(CommonInterface):
        """Common logic between Nft, Fungible and SingleAsset."""

        def __init__(self, metadata):
            CommonInterface.__init__(self)
            self.data.metadata = sp.cast(metadata, t.metadata)

        @sp.private(with_storage="read-only")
        def is_defined_(self, token_id):
            """Return True if the token is defined, else otherwise."""
            return self.data.token_metadata.contains(token_id)

        # Entrypoints

        @sp.entrypoint
        def update_operators(self, batch):
            """Accept a list of variants to add or remove operators who can perform
            transfers on behalf of the owner."""
            sp.cast(batch, t.update_operators_params)
            if self.private.policy.supports_operator:
                for action in batch:
                    with sp.match(action):
                        with sp.case.add_operator as operator:
                            _ = self.check_operator_update_permissions_(operator)
                            self.data.operators[operator] = ()
                        with sp.case.remove_operator as operator:
                            _ = self.check_operator_update_permissions_(operator)
                            del self.data.operators[operator]
            else:
                raise "FA2_OPERATORS_UNSUPPORTED"

        @sp.entrypoint
        def balance_of(self, params):
            """Send the balance of multiple account / token pairs to a callback
            address.

            `balance_` and `is_defined_` must be defined in the child class.
            """
            sp.cast(params, t.balance_of_params)

            @sp.effects(with_storage="read-write")
            def f_process_request(param):
                (req, is_defined, balance) = param
                sp.cast(req, t.balance_of_request)
                return sp.cast(
                    sp.record(
                        request=req,
                        balance=balance((is_defined, req)),
                    ),
                    t.balance_of_response,
                )

            answers = [
                f_process_request((x, self.is_defined_, self.balance_))
                for x in params.requests
            ]
            sp.transfer(answers, sp.mutez(0), params.callback)

        @sp.entrypoint
        def transfer(self, batch):
            """Accept a list of transfer operations between a source and multiple
            destinations.

            `transfer_tx_` and `is_defined_` must be defined in the child class.
            """
            sp.cast(batch, t.transfer_params)
            if self.private.policy.supports_transfer:
                for transfer in batch:
                    for tx in transfer.txs:
                        # The ordering of assert is important:
                        # 1) token_undefined, 2) transfer permission 3) balance
                        assert self.is_defined_(tx.token_id), "FA2_TOKEN_UNDEFINED"
                        _ = self.check_tx_transfer_permissions_(
                            sp.record(
                                from_=transfer.from_, to_=tx.to_, token_id=tx.token_id
                            )
                        )
                        if tx.amount > 0:
                            tx_params = sp.record(from_=transfer.from_, tx=tx)
                            self.transfer_tx_(tx_params)
            else:
                raise "FA2_TX_DENIED"

        # Offchain views

        @sp.offchain_view()
        def all_tokens(self):
            """Return the list of all the token IDs known to the contract."""
            return range(0, self.data.next_token_id)

        @sp.offchain_view()
        def is_operator(self, operator_permission):
            """Return whether `operator` is allowed to transfer `token_id` tokens
            owned by `owner`."""
            sp.cast(operator_permission, t.operator_permission)
            return self.is_operator_(operator_permission)

        @sp.offchain_view
        def get_balance(self, params):
            """Return the balance of an address for the specified `token_id`."""
            sp.cast(params, t.balance_of_request)
            return self.balance_((self.is_defined_, params))

        @sp.offchain_view()
        def total_supply(self, params):
            """Return the total number of tokens for the given `token_id`."""
            supply = self.supply_((self.is_defined_, params.token_id))
            return sp.cast(supply, sp.nat)

    ################
    # Base classes #
    ################

    class FungibleInterface(sp.Contract):
        def __init__(self):
            self.private.ledger_type = "Fungible"
            self.data.ledger = sp.cast(sp.big_map(), t.ledger_fungible)
            self.data.supply = sp.cast(sp.big_map(), t.supply_fungible)

    class Fungible(FungibleInterface, Common):
        def __init__(self, metadata, ledger, token_metadata):
            Common.__init__(self, metadata)
            FungibleInterface.__init__(self)

            for token_info in token_metadata:
                token_id = self.data.next_token_id
                self.data.token_metadata[token_id] = sp.record(
                    token_id=token_id, token_info=token_info
                )
                self.data.supply[token_id] = 0
                self.data.next_token_id += 1

            for token in ledger.items():
                token_id = sp.snd(token.key)
                assert self.data.token_metadata.contains(
                    token_id
                ), "The `ledger` parameter contains a key not contained in `token_metadata`."
                self.data.supply[token_id] += token.value
                self.data.ledger[token.key] = token.value

        @sp.private(with_storage="read-only")
        def balance_(self, params):
            (is_defined, balance_params) = params
            assert is_defined(balance_params.token_id), "FA2_TOKEN_UNDEFINED"
            return self.data.ledger.get(
                (balance_params.owner, balance_params.token_id), default=0
            )

        @sp.private(with_storage="read-write")
        def transfer_tx_(self, params):
            # Makes the transfer
            from_ = (params.from_, params.tx.token_id)
            self.data.ledger[from_] = sp.as_nat(
                self.data.ledger.get(from_, default=0) - params.tx.amount,
                error="FA2_INSUFFICIENT_BALANCE",
            )
            to_ = (params.tx.to_, params.tx.token_id)
            self.data.ledger[to_] = (
                self.data.ledger.get(to_, default=0) + params.tx.amount
            )

        @sp.private(with_storage="read-only")
        def supply_(self, params):
            (is_defined, token_id) = params
            assert is_defined(token_id), "FA2_TOKEN_UNDEFINED"
            return self.data.supply[token_id]

    ##########
    # Mixins #
    ##########

    class AdminInterface(sp.Contract):
        def __init__(self):
            self.data.administrator = sp.address("")

        @sp.private()
        def is_administrator_(self):
            raise "NotImplemented"
            return False

    class Admin(sp.Contract):
        """(Mixin) Provide the basics for having an administrator in the contract.

        Adds an `administrator` attribute in the storage. Provides a
        `set_administrator` entrypoint.
        """

        def __init__(self, administrator):
            self.data.administrator = administrator

        @sp.private(with_storage="read-only")
        def is_administrator_(self):
            return sp.sender == self.data.administrator

        @sp.entrypoint
        def set_administrator(self, administrator):
            """(Admin only) Set the contract administrator."""
            assert self.is_administrator_(), "FA2_NOT_ADMIN"
            self.data.administrator = administrator

    class WithdrawMutez(AdminInterface):
        """(Mixin) Provide an entrypoint to withdraw mutez that are in the
        contract's balance.

        Requires the `Admin` mixin.
        """

        def __init__(self):
            AdminInterface.__init__(self)

        @sp.entrypoint
        def withdraw_mutez(self, destination, amount):
            """(Admin only) Transfer `amount` mutez to `destination`."""
            assert self.is_administrator_(), "FA2_NOT_ADMIN"
            sp.send(destination, amount)

    class OffchainviewTokenMetadata(CommonInterface):
        """(Mixin) If present indexers use it to retrieve the token's metadata.

        Warning: If someone can change the contract's metadata they can change how
        indexers see every token metadata.
        """

        def __init__(self):
            CommonInterface.__init__(self)

        @sp.offchain_view()
        def token_metadata(self, token_id):
            """Returns the token-metadata URI for the given token."""
            assert self.data.token_metadata.contains(token_id), "FA2_TOKEN_UNDEFINED"
            return self.data.token_metadata[token_id]

    class OnchainviewBalanceOf(sp.Contract):
        """(Mixin) Non-standard onchain view equivalent to `balance_of`.

        Before onchain views were introduced in Michelson, the standard way
        of getting value from a contract was through a callback. Now that
        views are here we can create a view for the old style one.
        """

        @sp.private(with_storage="read-only")
        def balance_(self, params):
            raise "NotImplemented"
            return 0

        @sp.private(with_storage="read-only")
        def is_defined_(self, params):
            sp.cast(params, sp.nat)
            raise "NotImplemented"
            return False

        @sp.onchain_view()
        def get_balance_of(self, requests):
            """Onchain view equivalent to the `balance_of` entrypoint."""
            sp.cast(requests, sp.list[t.balance_of_request])

            @sp.effects(with_storage="read-only")
            def f_process_request_(param):
                (req, is_defined, balance) = param
                return sp.cast(
                    sp.record(
                        request=req,
                        balance=balance((is_defined, req)),
                    ),
                    t.balance_of_response,
                )

            return [
                f_process_request_((x, self.is_defined_, self.balance_))
                for x in requests
            ]

    class MintFungible(AdminInterface, FungibleInterface, CommonInterface):
        """(Mixin) Non-standard `mint` entrypoint for FA2Fungible with incrementing
        id.

        Requires the `Admin` mixin.
        """

        def __init__(self):
            CommonInterface.__init__(self)
            FungibleInterface.__init__(self)
            AdminInterface.__init__(self)

        @sp.entrypoint
        def mint(self, batch):
            """Admin can mint tokens."""
            sp.cast(
                batch,
                sp.list[
                    sp.record(
                        to_=sp.address,
                        token=sp.variant(
                            new=sp.map[sp.string, sp.bytes], existing=sp.nat
                        ),
                        amount=sp.nat,
                    ).layout(("to_", ("token", "amount")))
                ],
            )
            assert self.is_administrator_(), "FA2_NOT_ADMIN"
            for action in batch:
                with sp.match(action.token):
                    with sp.case.new as metadata:
                        token_id = self.data.next_token_id
                        self.data.token_metadata[token_id] = sp.record(
                            token_id=token_id, token_info=metadata
                        )
                        self.data.supply[token_id] = action.amount
                        self.data.ledger[(action.to_, token_id)] = action.amount
                        self.data.next_token_id += 1
                    with sp.case.existing as token_id:
                        assert self.is_defined_(token_id), "FA2_TOKEN_UNDEFINED"
                        self.data.supply[token_id] += action.amount
                        from_ = (action.to_, token_id)
                        self.data.ledger[from_] = (
                            self.data.ledger.get(from_, default=0) + action.amount
                        )

    class BurnFungible(AdminInterface, FungibleInterface, CommonInterface):
        """(Mixin) Non-standard `burn` entrypoint for FA2Fungible that uses the
        transfer policy permission."""

        def __init__(self):
            CommonInterface.__init__(self)
            FungibleInterface.__init__(self)
            AdminInterface.__init__(self)

        @sp.entrypoint
        def burn(self, batch):
            """Users can burn tokens if they have the transfer policy
            permission."""
            sp.cast(
                batch,
                sp.list[
                    sp.record(
                        from_=sp.address,
                        token_id=sp.nat,
                        amount=sp.nat,
                    ).layout(("from_", ("token_id", "amount")))
                ],
            )
            assert self.private.policy.supports_transfer, "FA2_TX_DENIED"
            for action in batch:
                assert self.is_defined_(action.token_id), "FA2_TOKEN_UNDEFINED"
                self.check_tx_transfer_permissions_(
                    sp.record(
                        from_=action.from_, to_=action.from_, token_id=action.token_id
                    )
                )
                from_ = (action.from_, action.token_id)
                # Burn the tokens
                self.data.ledger[from_] = sp.as_nat(
                    self.data.ledger.get(from_, default=0) - action.amount,
                    error="FA2_INSUFFICIENT_BALANCE",
                )

                is_supply = sp.is_nat(
                    self.data.supply.get(action.token_id, default=0) - action.amount
                )
                with sp.match(is_supply):
                    with sp.case.Some as supply:
                        self.data.supply[action.token_id] = supply
                    with None:
                        self.data.supply[action.token_id] = 0

    #########################
    # Non standard policies #
    #########################

###########
# Helpers #
###########


@sp.module
def Helpers():
    import t

    class TestReceiverBalanceOf(sp.Contract):
        """Helper used to test the `balance_of` entrypoint.

        Don't use it on-chain as it can be gas locked.
        """

        def __init__(self):
            self.last_known_balances = sp.big_map()

        @sp.entrypoint
        def receive_balances(self, params):
            sp.cast(params, list[t.balance_of_response])
            for resp in params:
                owner = (resp.request.owner, resp.request.token_id)
                if self.data.last_known_balances.contains(sp.sender):
                    self.data.last_known_balances[sp.sender][owner] = resp.balance
                else:
                    self.data.last_known_balances[sp.sender] = {owner: resp.balance}


def make_metadata(symbol, name, decimals):
    """Helper function to build metadata JSON bytes values."""
    return sp.map(
        l={
            "decimals": sp.scenario_utils.bytes_of_string("%d" % decimals),
            "name": sp.scenario_utils.bytes_of_string(name),
            "symbol": sp.scenario_utils.bytes_of_string(symbol),
        }
    )
