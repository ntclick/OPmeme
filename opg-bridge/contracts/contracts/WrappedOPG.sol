// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title Wrapped OPG (wOPG) — deployed on OG Testnet (10740)
/// @notice Mintable/burnable representation of OPG bridged from Base Sepolia.
contract WrappedOPG is ERC20, Ownable {
    address public bridge;

    event BridgeUpdated(address indexed oldBridge, address indexed newBridge);

    modifier onlyBridge() {
        require(msg.sender == bridge, "WrappedOPG: not bridge");
        _;
    }

    constructor() ERC20("Wrapped OPG", "wOPG") Ownable(msg.sender) {}

    function setBridge(address _bridge) external onlyOwner {
        require(_bridge != address(0), "WrappedOPG: zero bridge");
        emit BridgeUpdated(bridge, _bridge);
        bridge = _bridge;
    }

    function mint(address to, uint256 amount) external onlyBridge {
        _mint(to, amount);
    }

    function burn(address from, uint256 amount) external onlyBridge {
        _burn(from, amount);
    }
}
