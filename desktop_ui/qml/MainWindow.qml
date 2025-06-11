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
            
            GridView {
                width: parent.width
                height: parent.height
                leftMargin: 10
                rightMargin: 10
                topMargin: 10
                bottomMargin: 10
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