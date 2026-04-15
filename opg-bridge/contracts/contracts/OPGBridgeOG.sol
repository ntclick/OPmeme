// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

interface IWrappedOPG {
    function mint(address to, uint256 amount) external;
    function burn(address from, uint256 amount) external;
}

/// @title OPGBridgeOG — deployed on OG Testnet (10740)
/// @notice Mints wOPG when OPG is locked on Base Sepolia and burns wOPG to bridge back.
contract OPGBridgeOG is Ownable {
    IWrappedOPG public immutable wopg;
    address public relayer;

    uint256 public nonce;
    mapping(uint256 => bool) public processedNonces;

    event Burned(address indexed from, uint256 amount, uint256 nonce);
    event Minted(address indexed to, uint256 amount, uint256 nonce);
    event RelayerUpdated(address indexed oldRelayer, address indexed newRelayer);

    modifier onlyRelayer() {
        require(msg.sender == relayer, "Bridge: not relayer");
        _;
    }

    constructor(address _wopg, address _relayer) Ownable(msg.sender) {
        require(_wopg != address(0), "Bridge: zero wopg");
        require(_relayer != address(0), "Bridge: zero relayer");
        wopg = IWrappedOPG(_wopg);
        relayer = _relayer;
    }

    function setRelayer(address _relayer) external onlyOwner {
        require(_relayer != address(0), "Bridge: zero relayer");
        emit RelayerUpdated(relayer, _relayer);
        relayer = _relayer;
    }

    /// @notice Burn wOPG to bridge back to Base Sepolia.
    function burn(uint256 amount) external {
        require(amount > 0, "Bridge: zero amount");
        uint256 current = nonce;
        nonce = current + 1;
        wopg.burn(msg.sender, amount);
        emit Burned(msg.sender, amount, current);
    }

    /// @notice Mint wOPG to user after a Lock on Base Sepolia. Called by relayer.
    function mint(address to, uint256 amount, uint256 _nonce) external onlyRelayer {
        require(!processedNonces[_nonce], "Bridge: nonce processed");
        processedNonces[_nonce] = true;
        wopg.mint(to, amount);
        emit Minted(to, amount, _nonce);
    }
}
