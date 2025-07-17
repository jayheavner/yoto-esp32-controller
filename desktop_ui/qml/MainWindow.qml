import QtQuick 6.7
import QtQuick.Controls 6.7

Window {
    id: window
    width: 480
    height: 800
    visible: true
    color: "#111"
    title: "Yoto Card Library"

    property var selectedCard: null

    StackView {
        id: stackView
        anchors.fill: parent
        initialItem: gridView

        Component {
            id: gridView
            
            Item {
                width: stackView.width
                height: stackView.height
                
                // Now Playing icon - top right
                Item {
                    id: nowPlayingIcon
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.margins: 15
                    width: 60
                    height: 40
                    visible: coordinator.showNowPlaying
                    
                    // Sound wave bars (3 bars)
                    Row {
                        anchors.centerIn: parent
                        spacing: 3
                        
                        // Bar 1
                        Rectangle {
                            width: 4
                            height: coordinator.isPlaying ? 20 : 8
                            color: "#00ff88"
                            radius: 2
                            
                            Behavior on height {
                                NumberAnimation { duration: 300 }
                            }
                        }
                        
                        // Bar 2
                        Rectangle {
                            width: 4
                            height: coordinator.isPlaying ? 16 : 12
                            color: "#00ff88"
                            radius: 2
                            
                            Behavior on height {
                                NumberAnimation { duration: 400 }
                            }
                        }
                        
                        // Bar 3
                        Rectangle {
                            width: 4
                            height: coordinator.isPlaying ? 24 : 6
                            color: "#00ff88"
                            radius: 2
                            
                            Behavior on height {
                                NumberAnimation { duration: 350 }
                            }
                        }
                    }
                    
                    // Click area for navigation
                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            console.log("Now Playing icon clicked")
                            // Navigate to Now Playing view
                            stackView.push("NowPlayingView.qml", {
                                "cardId": coordinator.activeCardId,
                                "cardTitle": coordinator.currentCardTitle,
                                "cardImagePath": "" // Will be filled by actual card data
                            })
                        }
                    }
                }
                
                GridView {
                    anchors.fill: parent
                    anchors.margins: 10
                    anchors.topMargin: coordinator.showNowPlaying ? 70 : 10  // Space for Now Playing icon
                    cellWidth: 150
                    cellHeight: 200
                    model: imageModel

                    delegate: Image {
                        width: 140
                        height: 190
                        source: imagePath
                        fillMode: Image.PreserveAspectFit
                        
                        MouseArea {
                            anchors.fill: parent
                            onClicked: {
                                window.selectedCard = {
                                    imagePath: imagePath,
                                    cardId: cardId,
                                    title: cardTitle,
                                    index: index
                                }
                                stackView.push("DetailView.qml")
                            }
                        }
                    }
                }
            }
        }
    }
}