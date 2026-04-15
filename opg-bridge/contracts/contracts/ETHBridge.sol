// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @title ETHBridge — symmetric native ETH bridge.
/// @notice Deployed on BOTH chains. Each side holds an ETH liquidity pool.
///         User locks ETH on chain A → relayer releases ETH on chain B.
///         Both chains use ETH as native gas, so no wrapping is needed.
contract ETHBridge is Ownable {
    address public relayer;

    uint256 public nonce;
    mapping(uint256 => bool) public processedNonces;

    event LockedETH(address indexed from, uint256 amount, uint256 nonce);
    event ReleasedETH(address indexed to, uint256 amount, uint256 nonce);
    event RelayerUpdated(address indexed oldRelayer, address indexed newRelayer);
    event Seeded(address indexed by, uint256 amount);

    modifier onlyRelayer() {
        require(msg.sender == relayer, "Bridge: not relayer");
        _;
    }

    constructor(address _relayer) Ownable(msg.sender) {
        require(_relayer != address(0), "Bridge: zero relayer");
        relayer = _relayer;
    }

    function setRelayer(address _relayer) external onlyOwner {
        require(_relayer != address(0), "Bridge: zero relayer");
        emit RelayerUpdated(relayer, _relayer);
        relayer = _relayer;
    }

    /// @notice Owner-only liquidity injection. Used to bootstrap the pool so
    ///         the very first cross-chain transfer can be released.
    function seed() external payable onlyOwner {
        require(msg.value > 0, "Bridge: zero value");
        emit Seeded(msg.sender, msg.value);
    }

    /// @notice Lock native ETH to bridge it to the other chain.
    function lock() external payable {
        require(msg.value > 0, "Bridge: zero value");
        uint256 current = nonce;
        nonce = current + 1;
        emit LockedETH(msg.sender, msg.value, current);
    }

    /// @notice Release native ETH to user after a Lock on the other chain.
    function release(address payable to, uint256 amount, uint256 _nonce) external onlyRelayer {
        require(!processedNonces[_nonce], "Bridge: nonce processed");
        processedNonces[_nonce] = true;
        require(address(this).balance >= amount, "Bridge: insufficient liquidity");
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "Bridge: send failed");
        emit ReleasedETH(to, amount, _nonce);
    }

    /// @notice Owner-only withdrawal escape hatch (e.g. winding down testnet).
    function withdraw(address payable to, uint256 amount) external onlyOwner {
        require(address(this).balance >= amount, "Bridge: insufficient");
        (bool ok, ) = to.call{value: amount}("");
        require(ok, "Bridge: send failed");
    }

    /// @dev Accept plain transfers (treated as silent seeding).
    receive() external payable {}
}
