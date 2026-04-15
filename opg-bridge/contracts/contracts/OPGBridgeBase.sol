// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

/// @title OPGBridgeBase — deployed on Base Sepolia (84532)
/// @notice Locks real OPG on Base Sepolia and releases it back when wOPG is burned on OG.
contract OPGBridgeBase is Ownable {
    IERC20 public immutable opg;
    address public relayer;

    uint256 public nonce;
    mapping(uint256 => bool) public processedNonces;

    event Locked(address indexed from, uint256 amount, uint256 nonce);
    event Released(address indexed to, uint256 amount, uint256 nonce);
    event RelayerUpdated(address indexed oldRelayer, address indexed newRelayer);

    modifier onlyRelayer() {
        require(msg.sender == relayer, "Bridge: not relayer");
        _;
    }

    constructor(address _opg, address _relayer) Ownable(msg.sender) {
        require(_opg != address(0), "Bridge: zero opg");
        require(_relayer != address(0), "Bridge: zero relayer");
        opg = IERC20(_opg);
        relayer = _relayer;
    }

    function setRelayer(address _relayer) external onlyOwner {
        require(_relayer != address(0), "Bridge: zero relayer");
        emit RelayerUpdated(relayer, _relayer);
        relayer = _relayer;
    }

    /// @notice Lock OPG to bridge to OG Testnet. User must approve `amount` first.
    function lock(uint256 amount) external {
        require(amount > 0, "Bridge: zero amount");
        uint256 current = nonce;
        nonce = current + 1;
        require(opg.transferFrom(msg.sender, address(this), amount), "Bridge: transferFrom failed");
        emit Locked(msg.sender, amount, current);
    }

    /// @notice Release OPG back to user after a Burn on OG. Called by relayer.
    function release(address to, uint256 amount, uint256 _nonce) external onlyRelayer {
        require(!processedNonces[_nonce], "Bridge: nonce processed");
        processedNonces[_nonce] = true;
        require(opg.transfer(to, amount), "Bridge: transfer failed");
        emit Released(to, amount, _nonce);
    }
}
